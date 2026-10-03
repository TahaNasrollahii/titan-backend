from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import CartItem, Order
from .serializers import (
    CartItemUpdateSerializer,
    CartLineInputSerializer,
    CartMergeResponseSerializer,
    CartMergeSerializer,
    CartSerializer,
    CheckoutResponseSerializer,
    CheckoutSerializer,
    OrderSerializer,
)


def _cart_response(request, status_code=status.HTTP_200_OK) -> Response:
    summary = services.cart_summary(request.user)
    return Response(CartSerializer(summary, context={"request": request}).data, status=status_code)


@extend_schema(tags=["cart"])
class CartView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=CartSerializer)
    def get(self, request):
        return _cart_response(request)

    @extend_schema(responses=CartSerializer)
    def delete(self, request):
        services.clear_cart(request.user)
        return _cart_response(request)


@extend_schema(tags=["cart"], request=CartLineInputSerializer, responses={201: CartSerializer})
class CartItemCreateView(APIView):
    """Add a product (and optional variant) to the cart; adding an existing line increases its quantity."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CartLineInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.add_to_cart(request.user, data["product"], data.get("variant"), data["quantity"])
        return _cart_response(request, status.HTTP_201_CREATED)


@extend_schema(tags=["cart"])
class CartItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=CartItemUpdateSerializer, responses=CartSerializer)
    def patch(self, request, pk: int):
        get_object_or_404(CartItem, pk=pk, cart__user=request.user)
        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_cart_item(request.user, pk, serializer.validated_data["quantity"])
        return _cart_response(request)

    @extend_schema(responses=CartSerializer)
    def delete(self, request, pk: int):
        services.remove_cart_item(request.user, pk)
        return _cart_response(request)


@extend_schema(tags=["cart"], request=CartMergeSerializer, responses=CartMergeResponseSerializer)
class CartMergeView(APIView):
    """Merge a guest cart (kept in the browser before login) into the user's server-side cart."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CartMergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        skipped = services.merge_guest_cart(request.user, serializer.validated_data["items"])
        summary = services.cart_summary(request.user)
        payload = {"cart": summary, "skipped": skipped}
        return Response(CartMergeResponseSerializer(payload, context={"request": request}).data)


@extend_schema(tags=["orders"], request=CheckoutSerializer, responses={201: CheckoutResponseSerializer})
class CheckoutView(APIView):
    """Place an order from the cart.

    ``wallet``: paid immediately. ``gateway``: redirect the user to ``paymentUrl``; the order becomes
    paid when the gateway calls back.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        result = services.checkout(
            request.user,
            payment_method=serializer.validated_data["payment_method"],
            game_account=serializer.validated_data.get("game_account"),
        )
        order = services.user_orders(request.user).get(pk=result.order.pk)
        payload = {"order": order, "payment_url": result.payment_url}
        data = CheckoutResponseSerializer(payload, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["orders"])
class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Order.objects.none()  # model hint for the schema; real queryset is per-user
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "number"
    filterset_fields = ["status"]
    search_fields: list = []
    ordering_fields = ["created_at", "total"]

    def get_queryset(self):
        return services.user_orders(self.request.user)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def cancel(self, request, number=None):
        self.get_object()
        services.cancel_order(request.user, number)
        return Response(self.get_serializer(self.get_queryset().get(number=number)).data)
