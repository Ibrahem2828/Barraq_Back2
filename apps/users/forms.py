from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import User


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('email', 'full_name', 'phone_number', 'role')


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = (
            'email',
            'full_name',
            'phone_number',
            'role',
            'is_active',
            'is_staff',
            'is_superuser',
            'groups',
            'user_permissions',
        )
