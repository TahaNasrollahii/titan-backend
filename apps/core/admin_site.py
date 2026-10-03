"""Callbacks used by the admin theme (see ``config/settings/unfold.py``): environment label,
sidebar badges and the dashboard data."""

import json
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.accounts.models import User
from apps.catalog.models import Product, ProductVariant, Review
from apps.orders.models import Order, OrderItem
from apps.payments.models import Payment, Wallet
from apps.tournaments.models import Match, Registration, Tournament

from .admin import toman

REVENUE_DAYS = 14
KPI_WINDOW_DAYS = 30
LOW_STOCK_THRESHOLD = 5
TABLE_ROWS = 6

ORDER_STATUS_COLORS = {
    Order.Status.PENDING_PAYMENT: "#f59e0b",
    Order.Status.PAID: "#3b82f6",
    Order.Status.PROCESSING: "#8b5cf6",
    Order.Status.COMPLETED: "#22c55e",
    Order.Status.CANCELLED: "#9ca3af",
    Order.Status.FAILED: "#ef4444",
    Order.Status.REFUNDED: "#64748b",
}


# ------------------------------------------------------------------ small callbacks
def environment_callback(request):
    return [_("Development"), "warning"] if settings.DEBUG else [_("Production"), "danger"]


def frontend_url(request):
    return settings.FRONTEND_URL


def orders_to_fulfil_badge(request):
    count = Order.objects.filter(status__in=[Order.Status.PAID, Order.Status.PROCESSING]).count()
    return count or None


def live_matches_badge(request):
    return Match.objects.filter(status=Match.Status.LIVE).count() or None


def hidden_reviews_badge(request):
    return Review.objects.filter(is_approved=False).count() or None


# ------------------------------------------------------------------ dashboard
def _change(current: int, previous: int) -> dict:
    """Percentage change vs the previous period, ready for the KPI card footer."""
    if not previous:
        return {"text": _("new") if current else "—", "positive": current >= previous}
    pct = round((current - previous) * 100 / previous)
    return {"text": f"{pct:+d}%", "positive": pct >= 0}


def _changelist(model_label: str, **filters) -> str:
    url = reverse(f"admin:{model_label}_changelist")
    if filters:
        url += "?" + "&".join(f"{key}={value}" for key, value in filters.items())
    return url


def _kpis(now):
    window = timedelta(days=KPI_WINDOW_DAYS)
    paid = Order.objects.filter(status__in=Order.PAID_STATUSES)

    revenue_now = paid.filter(paid_at__gte=now - window).aggregate(total=Sum("total"))["total"] or 0
    revenue_before = (
        paid.filter(paid_at__gte=now - 2 * window, paid_at__lt=now - window).aggregate(total=Sum("total"))[
            "total"
        ]
        or 0
    )
    users_now = User.objects.filter(date_joined__gte=now - window).count()
    users_before = User.objects.filter(
        date_joined__gte=now - 2 * window, date_joined__lt=now - window
    ).count()
    to_fulfil = Order.objects.filter(status__in=[Order.Status.PAID, Order.Status.PROCESSING]).count()
    live_tournaments = Tournament.objects.filter(state=Tournament.State.LIVE).count()
    live_matches = Match.objects.filter(status=Match.Status.LIVE).count()

    return [
        {
            "title": _("Revenue · last 30 days"),
            "icon": "payments",
            "value": toman(revenue_now),
            "change": _change(revenue_now, revenue_before),
            "footer": _("vs previous 30 days"),
            "link": _changelist("orders_order"),
        },
        {
            "title": _("Orders to fulfil"),
            "icon": "local_shipping",
            "value": f"{to_fulfil:,}",
            "change": None,
            "footer": _("paid or processing"),
            "link": _changelist("orders_order", status__in="paid,processing"),
        },
        {
            "title": _("New players · last 30 days"),
            "icon": "person_add",
            "value": f"{users_now:,}",
            "change": _change(users_now, users_before),
            "footer": _("vs previous 30 days"),
            "link": _changelist("accounts_user"),
        },
        {
            "title": _("Live tournaments"),
            "icon": "emoji_events",
            "value": f"{live_tournaments:,}",
            "change": None,
            "footer": ngettext(
                "%(count)s match live right now", "%(count)s matches live right now", live_matches
            )
            % {"count": live_matches},
            "link": _changelist("tournaments_tournament", state__exact="live"),
        },
    ]


def _revenue_chart(now) -> str:
    start = (now - timedelta(days=REVENUE_DAYS - 1)).date()
    rows = (
        Order.objects.filter(status__in=Order.PAID_STATUSES, paid_at__date__gte=start)
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(total=Sum("total"), orders=Count("id"))
    )
    by_day = {row["day"]: row for row in rows}
    days = [start + timedelta(days=offset) for offset in range(REVENUE_DAYS)]
    return json.dumps(
        {
            "labels": [day.strftime("%b %d") for day in days],
            "datasets": [
                {
                    "label": str(_("Revenue (toman)")),
                    "data": [by_day.get(day, {}).get("total") or 0 for day in days],
                    "backgroundColor": "#e2453f",
                    "borderRadius": 6,
                    "maxBarThickness": 28,
                },
            ],
        }
    )


def _order_status_breakdown() -> list[dict]:
    counts = dict(Order.objects.values_list("status").annotate(n=Count("id")))
    total = sum(counts.values()) or 1
    return [
        {
            "label": label,
            "count": counts.get(value, 0),
            "percent": round(counts.get(value, 0) * 100 / total),
            "color": ORDER_STATUS_COLORS[value],
            "link": _changelist("orders_order", status__exact=value),
        }
        for value, label in Order.Status.choices
        if counts.get(value)
    ]


def _latest_orders() -> list[dict]:
    orders = Order.objects.select_related("user").order_by("-created_at")[:TABLE_ROWS]
    return [
        {
            "number": order.number,
            "link": reverse("admin:orders_order_change", args=[order.pk]),
            "customer": order.user.display_name,
            "total": toman(order.total),
            "status": order.get_status_display(),
            "status_value": order.status,
            "created": order.created_at,
        }
        for order in orders
    ]


def _upcoming_tournaments(now) -> list[dict]:
    tournaments = (
        Tournament.objects.filter(state__in=[Tournament.State.SCHEDULED, Tournament.State.LIVE])
        .select_related("game")
        .annotate(
            confirmed=Count("registrations", filter=Q(registrations__status=Registration.Status.CONFIRMED))
        )
        .order_by("starts_at")[:TABLE_ROWS]
    )
    return [
        {
            "title": t.title,
            "link": reverse("admin:tournaments_tournament_change", args=[t.pk]),
            "game": t.game.title_en,
            "status": t.public_status,
            "status_label": Tournament.Status(t.public_status).label,
            "filled": f"{t.confirmed}/{t.max_participants}",
            "percent": min(100, round(t.confirmed * 100 / t.max_participants)),
            "starts": t.starts_at,
        }
        for t in tournaments
    ]


def _low_stock() -> list[dict]:
    variants = (
        ProductVariant.objects.filter(stock__lte=LOW_STOCK_THRESHOLD, product__is_active=True)
        .select_related("product")
        .order_by("stock")[:TABLE_ROWS]
    )
    products = Product.objects.filter(
        is_active=True, has_variants=False, stock__lte=LOW_STOCK_THRESHOLD
    ).order_by("stock")[:TABLE_ROWS]
    items = [
        {
            "title": f"{v.product.title} — {v.label}",
            "stock": v.stock,
            "link": reverse("admin:catalog_product_change", args=[v.product_id]),
        }
        for v in variants
    ] + [
        {"title": p.title, "stock": p.stock, "link": reverse("admin:catalog_product_change", args=[p.pk])}
        for p in products
    ]
    return sorted(items, key=lambda item: item["stock"])[:TABLE_ROWS]


def _top_products() -> list[dict]:
    rows = (
        OrderItem.objects.filter(order__status__in=Order.PAID_STATUSES, product__isnull=False)
        .values("product_id", "title")
        .annotate(units=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:TABLE_ROWS]
    )
    return [
        {
            "title": row["title"],
            "units": row["units"],
            "revenue": toman(row["revenue"]),
            "link": reverse("admin:catalog_product_change", args=[row["product_id"]]),
        }
        for row in rows
    ]


def dashboard_callback(request, context):
    now = timezone.now()
    context.update(
        {
            "kpis": _kpis(now),
            "revenue_chart": _revenue_chart(now),
            "order_statuses": _order_status_breakdown(),
            "latest_orders": _latest_orders(),
            "upcoming_tournaments": _upcoming_tournaments(now),
            "low_stock": _low_stock(),
            "top_products": _top_products(),
            "wallet_float": toman(Wallet.objects.aggregate(total=Sum("balance"))["total"]),
            "pending_payments": Payment.objects.filter(status=Payment.Status.INITIATED).count(),
            "unapproved_reviews": Review.objects.filter(is_approved=False).count(),
            "low_stock_threshold": LOW_STOCK_THRESHOLD,
            "quick_links": [
                {"title": _("Add product"), "icon": "add_box", "link": reverse("admin:catalog_product_add")},
                {
                    "title": _("New tournament"),
                    "icon": "emoji_events",
                    "link": reverse("admin:tournaments_tournament_add"),
                },
                {"title": _("New promo"), "icon": "campaign", "link": reverse("admin:content_promo_add")},
                {
                    "title": _("Announcement"),
                    "icon": "notifications_active",
                    "link": reverse("admin:content_announcement_add"),
                },
            ],
        }
    )
    return context
