from django.db import DatabaseError, connection

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


@extend_schema(
    tags=["system"],
    responses=inline_serializer(
        "Health", {"status": serializers.CharField(), "database": serializers.CharField()}
    ),
)
class HealthCheckView(APIView):
    """Liveness/readiness probe: confirms the process is up and the database answers."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except DatabaseError:
            return Response({"status": "error", "database": "unavailable"}, status=503)
        return Response({"status": "ok", "database": "ok"})
