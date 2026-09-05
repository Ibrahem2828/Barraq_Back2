import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.admin_dashboard.services import assign_roles_to_user, seed_default_rbac
from apps.subscriptions.services import ensure_default_plans


class Command(BaseCommand):
    help = "Idempotently seed production reference data and optionally provision the first super admin."

    def add_arguments(self, parser):
        parser.add_argument("--create-superuser-from-env", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        permissions, roles = seed_default_rbac()
        plans = ensure_default_plans()
        self.stdout.write(self.style.SUCCESS(f"RBAC ready: {len(permissions)} permissions, {len(roles)} roles."))
        self.stdout.write(self.style.SUCCESS(f"Subscription plans ready: {len(plans)}."))

        if not options["create_superuser_from_env"]:
            return
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip().lower()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")
        full_name = os.getenv("DJANGO_SUPERUSER_FULL_NAME", "Baraq Super Admin")
        if not email or not password:
            raise CommandError("DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD are required.")
        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"full_name": full_name, "is_staff": True, "is_superuser": True, "role": User.Roles.SUPER_ADMIN},
        )
        if created:
            user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.role = User.Roles.SUPER_ADMIN
        user.save()
        assign_roles_to_user(user, [roles["super_admin"]], assigned_by=user)
        self.stdout.write(self.style.SUCCESS(f"Super admin ready: {email}."))
