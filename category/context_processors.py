from .models import Category


#to make categories link
def menu_links(request):
    links = Category.objects.all()
    return dict(links=links)
