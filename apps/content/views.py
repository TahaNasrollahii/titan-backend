from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Announcement, ContactChannel, Guide, Promo, SiteSetting
from .serializers import (
    AnnouncementSerializer,
    ContactInfoSerializer,
    GuideDetailSerializer,
    GuideSummarySerializer,
    PromoSerializer,
)

SUPPORT_ONLINE_KEY = "support_online"


@extend_schema(
    tags=["content"],
    parameters=[OpenApiParameter("placement", str, enum=Promo.Placement.values)],
)
class PromoListView(generics.ListAPIView):
    serializer_class = PromoSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        queryset = Promo.objects.live().select_related("product", "tournament")
        placement = self.request.query_params.get("placement")
        return queryset.filter(placement=placement) if placement else queryset


@extend_schema(tags=["content"])
class AnnouncementListView(generics.ListAPIView):
    queryset = Announcement.objects.live()
    serializer_class = AnnouncementSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []


@extend_schema(tags=["content"], responses=ContactInfoSerializer)
class ContactInfoView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        payload = {
            "support_online": bool(SiteSetting.get(SUPPORT_ONLINE_KEY, True)),
            "channels": ContactChannel.objects.filter(is_active=True),
        }
        return Response(ContactInfoSerializer(payload, context={"request": request}).data)


@extend_schema(
    tags=["content"],
    parameters=[
        OpenApiParameter("category", str, enum=Guide.Category.values),
        OpenApiParameter("game", str, description="Game slug."),
        OpenApiParameter("product", str, description="Product slug."),
    ],
)
class GuideListView(generics.ListAPIView):
    serializer_class = GuideSummarySerializer
    permission_classes = [AllowAny]
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        queryset = Guide.objects.published().select_related("game")
        params = self.request.query_params
        if category := params.get("category"):
            queryset = queryset.filter(category=category)
        if game := params.get("game"):
            queryset = queryset.filter(game__slug=game)
        if product := params.get("product"):
            queryset = queryset.filter(products__slug=product)
        return queryset


@extend_schema(tags=["content"])
class GuideDetailView(generics.RetrieveAPIView):
    queryset = Guide.objects.published().select_related("game").prefetch_related("faqs", "products")
    serializer_class = GuideDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
