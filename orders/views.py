from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from carts.models import CartItem
from .forms import OrderForm
import datetime
from djshop import settings
from .models import Order, Payment, OrderProduct
import json
from store.models import Product
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
import razorpay
from urllib.parse import urlencode




# Create your views here.
razorpay_client = razorpay.Client(auth=(settings.razorpay_id, settings.razorpay_account_id))
def place_order(request, total=0, quantity=0,):
    current_user = request.user

    # If the cart count is less than or equal to 0, then redirect back to store
    cart_items = CartItem.objects.filter(user=current_user)
    cart_count = cart_items.count()
    if cart_count <= 0:
        return redirect('store')

    grand_total = 0
    tax = 0
    for cart_item in cart_items:
        total += (cart_item.product.price * cart_item.quantity)
        quantity += cart_item.quantity
    tax = 0.18*total
    grand_total = total + tax
    

    if request.method == 'POST':
        form = OrderForm(request.POST)
        print(request.body)
        if form.is_valid():
            # Store all the billing information inside Order table
            data = Order()
            data.user = current_user
            data.first_name = form.cleaned_data['first_name']
            data.last_name = form.cleaned_data['last_name']
            data.phone = form.cleaned_data['phone']
            data.email = form.cleaned_data['email']
            data.address_line_1 = form.cleaned_data['address_line_1']
            data.address_line_2 = form.cleaned_data['address_line_2']
            data.country = form.cleaned_data['country']
            data.state = form.cleaned_data['state']
            data.city = form.cleaned_data['city']
            data.order_note = form.cleaned_data['order_note']
            data.order_total = grand_total
            data.tax = tax
            data.ip = request.META.get('REMOTE_ADDR')#getting ip address
            data.save()
            
            # Generating order number
            yr = int(datetime.date.today().strftime('%Y'))
            dt = int(datetime.date.today().strftime('%d'))
            mt = int(datetime.date.today().strftime('%m'))
            d = datetime.date(yr,mt,dt)
            current_date = d.strftime("%Y%m%d") 
            #as we have already saved data so we getting order id
            order_number = current_date + str(data.id)
            data.order_number = order_number
            data.save()

            order = Order.objects.get(user=current_user, is_ordered=False, order_number=order_number)
            notes={
                'order_number':order_number
            }
            
            razorpay_order = razorpay_client.order.create(dict(amount=grand_total*100, currency='INR', notes = notes, receipt=str(order.id), payment_capture='0'))
            context = {
                'order': order,
                'cart_items': cart_items,
                'total': total,
                'tax': tax,
                'grand_total': grand_total,
                'razorpay_merchant_id':settings.razorpay_id,
                'razorpay_order_id':razorpay_order['id']
            }
            print(context)
            
            # {'order_id': razorpay_order['id'], 'final_price':amt ,, }
            return render(request, 'orders/payments.html', context)
        else:
            print(form.errors)
            return render(request, 'store/checkout.html', {'form':form})


    else:
        return redirect('checkout')

@csrf_exempt
def payments(request):
    data = {}
    try:

        transID = request.POST.get('razorpay_payment_id', '')
        payment = razorpay_client.payment.fetch(transID)
        order_number = payment['notes'].get('order_number')
        transaction_status = payment['status']
        request.session['order_number'] =order_number
        request.session['transID']=transID
        request.session['transaction_status']=transaction_status
        print(order_number,transID,'HI',transaction_status)
        
    except:
        pass

    order = Order.objects.get(user=request.user, is_ordered=False, order_number=order_number)
    # Store transaction details inside Payment model
    payment = Payment(
        user = request.user,
        payment_id = transID,
        payment_method = 'Razorpay',
        amount_paid = order.order_total,
        status = transaction_status,
    )
    payment.save()

    order.payment = payment
    order.is_ordered = True
    order.save()

    # Move the cart items to Order Product table
    cart_items = CartItem.objects.filter(user=request.user)

    for item in cart_items:
        orderproduct = OrderProduct()
        orderproduct.order_id = order.id
        orderproduct.payment = payment
        orderproduct.user_id = request.user.id
        orderproduct.product_id = item.product_id
        orderproduct.quantity = item.quantity
        orderproduct.product_price = item.product.price
        orderproduct.ordered = True
        orderproduct.save()

        #setting product variants after saving the orderproduct and getting the id
        #as this a many to many feilds so we cant save before getting the order product saved id
        cart_item = CartItem.objects.get(id=item.id)
        product_variation = cart_item.variations.all()
        orderproduct = OrderProduct.objects.get(id=orderproduct.id)
        orderproduct.variations.set(product_variation)
        orderproduct.save()


        # Reduce the quantity of the sold products
        product = Product.objects.get(id=item.product_id)
        product.stock -= item.quantity
        product.save()

    # Clearin the cart
    CartItem.objects.filter(user=request.user).delete()

    # Send order recieved email to customer
    mail_subject = 'Thank you for your order!'
    message = render_to_string('orders/order_recieved_email.html', {
        'user': request.user,
        'order': order,
    })
    to_email = request.user.email
    send_email = EmailMessage(mail_subject, message, to=[to_email])
    print(send_email)


    # Send order number and transaction id back to sendData method via JsonResponse
    # data = {
    #     'order_number': order.order_number,
    #     'transID': payment.payment_id,
    # }
    redirect_url = f'/order_complete/?{urlencode(data)}'
    #returning the required data back to the send data fucntion
    return redirect('order_complete')


def order_complete(request):
    
    order_number=request.session.get('order_number')
    transID=request.session.get('transID')
    print('at orderc',request.session)

    try:
        order = Order.objects.get(order_number=order_number, is_ordered=True)
        ordered_products = OrderProduct.objects.filter(order_id=order.id)

        subtotal = 0
        for i in ordered_products:
            subtotal += i.product_price * i.quantity

        payment = Payment.objects.get(payment_id=transID)

        context = {
            'order': order,
            'ordered_products': ordered_products,
            'order_number': order.order_number,
            'transID': payment.payment_id,
            'payment': payment,
            'subtotal': subtotal,
        }

        return render(request, 'orders/order_complete.html', context)
    except Exception as e:
        #if someone randomly tries to put wrong order number or transid
        print(e)
        return redirect('home')