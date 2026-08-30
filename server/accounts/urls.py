from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="auth-login"),
    path("change-password/", views.change_password_view, name="auth-change-password"),
    path("me/", views.me_view, name="auth-me"),
]
