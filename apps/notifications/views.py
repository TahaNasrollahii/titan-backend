from django.shortcuts import get_object_or_404

from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import Notification
from .serializers import NotificationSerializer

_count_response = inline_serializer("UnreadCount", {"count": serializers.IntegerField()})


@extend_schema(tags=["notifications"])
class NotificationViewSet(mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Notification.objects.none()  # model hint for the schema; real queryset is per-user
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends: list = []

    def get_queryset(self):
        queryset = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") in {"1", "true"}:
            queryset = queryset.filter(is_read=False)
        return queryset

    @extend_schema(parameters=[OpenApiParameter("unread", bool, description="Only unread notifications.")])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses=_count_response)
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"count": Notification.objects.filter(user=request.user, is_read=False).count()})

    @extend_schema(request=None, responses=NotificationSerializer)
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        get_object_or_404(self.get_queryset(), pk=pk)
        notification = services.mark_read(request.user, int(pk))
        return Response(self.get_serializer(notification).data)

    @extend_schema(request=None, responses=_count_response)
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        return Response({"count": services.mark_all_read(request.user)})
