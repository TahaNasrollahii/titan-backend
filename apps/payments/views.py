import logging

from django.shortcuts import redirect

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import DomainError

from . import services
from .models import Payment, WalletTransaction
from .serializers import (
    PaymentSerializer,
    PaymentStartSerializer,
    TopupSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["wallet"])
class WalletView(generics.RetrieveAPIView):
    serializer_class = WalletSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return services.get_wallet(self.request.user)


@extend_schema(tags=["wallet"])
class WalletTransactionListView(generics.ListAPIView):
    queryset = WalletTransaction.objects.none()  # model hint for the schema; real queryset is per-user
    serializer_class = WalletTransactionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["kind"]
    ordering_fields: list = []
    search_fields: list = []

    def get_queryset(self):
        return WalletTransaction.objects.filter(wallet__user=self.request.user)


@extend_schema(tags=["wallet"], request=TopupSerializer, responses={201: PaymentStartSerializer})
class WalletTopupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TopupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start = services.start_topup(request.user, serializer.validated_data["amount"])
        return Response(PaymentStartSerializer(start).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["payments"])
class PaymentDetailView(generics.RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(user=self.request.user)


@extend_schema(
    tags=["payments"],
    parameters=[
        OpenApiParameter("Authority", str, required=True),
        OpenApiParameter("Status", str, required=True, enum=["OK", "NOK"]),
    ],
    responses={302: None},
)
class PaymentCallbackView(APIView):
    """Gateway return URL. Verifies the payment, then redirects the browser to the frontend result page."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        authority = request.query_params.get("Authority", "")
        gateway_status = request.query_params.get("Status", "")
        try:
            payment = services.process_gateway_callback(authority, gateway_status)
        except DomainError:
            logger.warning("Payment callback failed for authority %r", authority, exc_info=True)
            return redirect(services.frontend_result_url(None, status=Payment.Status.FAILED))
        return redirect(services.frontend_result_url(payment))
