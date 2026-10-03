from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Announcement, ContactChannel, Promo, SiteSetting
from .serializers import AnnouncementSerializer, ContactInfoSerializer, PromoSerializer

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
