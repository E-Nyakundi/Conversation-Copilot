from django.urls import path

from . import admin_views

urlpatterns = [
    path("users/", admin_views.admin_users_view, name="admin-users"),
    path("users/<int:user_id>/", admin_views.admin_user_detail_view, name="admin-user-detail"),
    path("users/<int:user_id>/quota/", admin_views.admin_user_quota_view, name="admin-user-quota"),
    path("users/<int:user_id>/revoke/", admin_views.admin_user_revoke_view, name="admin-user-revoke"),
    path(
        "users/<int:user_id>/reactivate/",
        admin_views.admin_user_reactivate_view,
        name="admin-user-reactivate",
    ),
]
