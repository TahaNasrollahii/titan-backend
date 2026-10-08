"""Admin theme (django-unfold): branding, Titan colour palette and sidebar navigation."""

from django.templatetags.static import static

from config.lazy import gettext_lazy as _
from config.lazy import reverse_lazy

CALLBACKS = "apps.core.admin_site"


def _changelist(app_label: str, model: str):
    return reverse_lazy(f"admin:{app_label}_{model}_changelist")


def _can_view(app_label: str, model: str):
    """Show a sidebar item only to staff allowed to view that model."""
    return lambda request: request.user.has_perm(f"{app_label}.view_{model}")


def _item(title, icon: str, app_label: str, model: str, badge: str | None = None) -> dict:
    item = {
        "title": title,
        "icon": icon,
        "link": _changelist(app_label, model),
        "permission": _can_view(app_label, model),
    }
    if badge:
        item["badge"] = badge
    return item


# Titan red (#e2453f) as the primary scale; warm, wine-tinted neutrals as the base scale so the
# dark theme matches the storefront's burgundy panels.
TITAN_PRIMARY = {
    "50": "#fef3f2",
    "100": "#fde5e3",
    "200": "#fbd0cc",
    "300": "#f7aea8",
    "400": "#f07d74",
    "500": "#e2453f",
    "600": "#cf2f29",
    "700": "#ad241f",
    "800": "#8f221e",
    "900": "#77221f",
    "950": "#410d0b",
}
TITAN_BASE = {
    "50": "#fbf7f7",
    "100": "#f5eded",
    "200": "#e9dcdd",
    "300": "#d6c0c2",
    "400": "#b09396",
    "500": "#8a6a6e",
    "600": "#6b4b50",
    "700": "#52363b",
    "800": "#3f1f26",
    "900": "#2e131a",
    "950": "#1c0a0f",
}

UNFOLD = {
    "SITE_TITLE": "Titan Admin",
    "SITE_HEADER": "Titan",
    "SITE_SUBHEADER": _("Store & esports back office"),
    "SITE_ICON": {
        "light": lambda request: static("admin/titan/logo-light.svg"),
        "dark": lambda request: static("admin/titan/logo-dark.svg"),
    },
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "any",
            "type": "image/svg+xml",
            "href": lambda request: static("admin/titan/favicon.svg"),
        },
    ],
    "SITE_DROPDOWN": [
        {"icon": "storefront", "title": _("Storefront"), "link": f"{CALLBACKS}.frontend_url"},
        {"icon": "api", "title": _("API documentation"), "link": reverse_lazy("swagger-ui")},
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_BACK_BUTTON": True,
    "ENVIRONMENT": f"{CALLBACKS}.environment_callback",
    "DASHBOARD_CALLBACK": f"{CALLBACKS}.dashboard_callback",
    "STYLES": [lambda request: static("admin/titan/titan.css")],
    "LOGIN": {
        "image": lambda request: static("admin/titan/login.png"),
    },
    "COLORS": {
        "base": TITAN_BASE,
        "primary": TITAN_PRIMARY,
    },
    "COMMAND": {
        "search_models": True,
        "show_history": True,
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "items": [
                    {"title": _("Dashboard"), "icon": "space_dashboard", "link": reverse_lazy("admin:index")},
                ],
            },
            {
                "title": _("Sales"),
                "separator": True,
                "collapsible": True,
                "items": [
                    _item(
                        _("Orders"), "receipt_long", "orders", "order", f"{CALLBACKS}.orders_to_fulfil_badge"
                    ),
                    _item(_("Payments"), "credit_card", "payments", "payment"),
                    _item(_("Wallets"), "account_balance_wallet", "payments", "wallet"),
                    _item(_("Wallet ledger"), "receipt", "payments", "wallettransaction"),
                    _item(_("Carts"), "shopping_cart", "orders", "cart"),
                ],
            },
            {
                "title": _("Store"),
                "separator": True,
                "collapsible": True,
                "items": [
                    _item(_("Products"), "inventory_2", "catalog", "product"),
                    _item(_("Games"), "sports_esports", "catalog", "game"),
                    _item(_("Categories"), "category", "catalog", "productcategory"),
                    _item(_("Platforms"), "devices", "catalog", "platform"),
                    _item(_("Reviews"), "reviews", "catalog", "review", f"{CALLBACKS}.hidden_reviews_badge"),
                    _item(_("Wishlists"), "favorite", "catalog", "wishlistitem"),
                ],
            },
            {
                "title": _("Esports"),
                "separator": True,
                "collapsible": True,
                "items": [
                    _item(_("Tournaments"), "emoji_events", "tournaments", "tournament"),
                    _item(_("Matches"), "swords", "tournaments", "match", f"{CALLBACKS}.live_matches_badge"),
                    _item(_("Registrations"), "how_to_reg", "tournaments", "registration"),
                    _item(_("Seasons"), "calendar_month", "tournaments", "season"),
                    _item(_("Player stats"), "leaderboard", "tournaments", "playerstats"),
                    _item(_("Team stats"), "bar_chart", "tournaments", "teamstats"),
                ],
            },
            {
                "title": _("Community"),
                "separator": True,
                "collapsible": True,
                "items": [
                    _item(_("Users"), "person", "accounts", "user"),
                    _item(_("Teams"), "groups", "teams", "team"),
                    _item(_("Team invitations"), "mail", "teams", "teaminvitation"),
                    _item(_("Friendships"), "diversity_3", "accounts", "friendship"),
                    _item(_("Game accounts"), "key", "accounts", "gameaccount"),
                    _item(_("Badges"), "military_tech", "accounts", "badge"),
                    _item(_("Rank tiers"), "workspace_premium", "accounts", "ranktier"),
                ],
            },
            {
                "title": _("Content"),
                "separator": True,
                "collapsible": True,
                "items": [
                    _item(_("Promos & banners"), "campaign", "content", "promo"),
                    _item(_("Announcements"), "notifications_active", "content", "announcement"),
                    _item(_("Contact channels"), "support_agent", "content", "contactchannel"),
                    _item(_("Notifications"), "notifications", "notifications", "notification"),
                    _item(_("Site settings"), "tune", "content", "sitesetting"),
                ],
            },
            {
                "title": _("Security"),
                "separator": True,
                "collapsible": True,
                "items": [
                    _item(_("Groups & roles"), "admin_panel_settings", "auth", "group"),
                    _item(_("OTP codes"), "password", "accounts", "otpcode"),
                    _item(_("Active sessions (tokens)"), "token", "token_blacklist", "outstandingtoken"),
                    _item(_("Revoked tokens"), "block", "token_blacklist", "blacklistedtoken"),
                ],
            },
        ],
    },
}
