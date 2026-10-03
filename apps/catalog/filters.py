import django_filters

from .models import Product


class ProductFilter(django_filters.FilterSet):
    game = django_filters.CharFilter(field_name="game__slug")
    category = django_filters.CharFilter(field_name="category__slug")
    platform = django_filters.CharFilter(field_name="platforms__slug", distinct=True)
    price_min = django_filters.NumberFilter(field_name="price", lookup_expr="gte")
    price_max = django_filters.NumberFilter(field_name="price", lookup_expr="lte")
    in_stock = django_filters.BooleanFilter(method="filter_in_stock")
    on_sale = django_filters.BooleanFilter(method="filter_on_sale")

    class Meta:
        model = Product
        fields = ["game", "category", "platform", "price_min", "price_max", "in_stock", "on_sale"]

    def filter_in_stock(self, queryset, name, value):
        available = queryset.filter(stock__isnull=True) | queryset.filter(stock__gt=0)
        return available if value else queryset.filter(stock=0)

    def filter_on_sale(self, queryset, name, value):
        return queryset.filter(original_price__isnull=not value)
