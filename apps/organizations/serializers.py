from __future__ import annotations

from rest_framework import serializers

from .models import (
    ClassMembership,
    Classroom,
    Invitation,
    JoinRequest,
    Organization,
    OrganizationMembership,
)


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = (
            "public_id",
            "name",
            "organization_type",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "created_at", "updated_at")


class OrganizationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ("name", "organization_type", "status")


class ClassroomSerializer(serializers.ModelSerializer):
    # The owning organization is exposed as its public id, never accepted from
    # the client on write -- see ClassroomViewSet.perform_create, which derives
    # it from the caller's scope instead.
    organization: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Classroom
        fields = (
            "public_id",
            "organization",
            "name",
            "code",
            "status",
            "member_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "organization", "created_at", "updated_at")


class ClassroomWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Classroom
        fields = ("name", "code", "status")


class ClassMembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)

    class Meta:
        model = ClassMembership
        fields = (
            "id",
            "classroom",
            "user",
            "user_email",
            "user_full_name",
            "status",
            "joined_at",
            "removed_at",
            "created_at",
        )
        read_only_fields = fields


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)
    organization: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = (
            "id",
            "organization",
            "user",
            "user_email",
            "user_full_name",
            "member_type",
            "status",
            "joined_at",
            "removed_at",
            "created_at",
        )
        read_only_fields = fields


class InvitationSerializer(serializers.ModelSerializer):
    """Manager-facing view of an invitation.

    Includes the code and token, because the manager is the one who has to
    hand them out. Nothing else in the API ever returns them -- the learner's
    preview deliberately echoes back neither.
    """

    organization: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)

    class Meta:
        model = Invitation
        fields = (
            "id",
            "organization",
            "classroom",
            "token",
            "code",
            "status",
            "expires_at",
            "max_uses",
            "usage_count",
            "created_at",
        )
        read_only_fields = (
            "id",
            "organization",
            "classroom",
            "token",
            "code",
            "usage_count",
            "created_at",
        )


class InvitationCreateSerializer(serializers.Serializer):
    """The classroom is addressed by public id and re-resolved inside the
    caller's scope; a body value can name a class but never reach one."""

    classroom = serializers.UUIDField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    max_uses = serializers.IntegerField(required=False, min_value=0, max_value=10000, default=0)


class JoinRequestSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)
    organization: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field="public_id", read_only=True)

    class Meta:
        model = JoinRequest
        fields = (
            "public_id",
            "user",
            "user_email",
            "user_full_name",
            "organization",
            "classroom",
            "status",
            "decided_at",
            "created_at",
        )
        read_only_fields = fields


class JoinPreviewRequestSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, allow_blank=True, max_length=200)
    code = serializers.CharField(required=False, allow_blank=True, max_length=40)

    def validate(self, attrs):
        if not attrs.get("token") and not attrs.get("code"):
            raise serializers.ValidationError({"invitation": "يلزم إدخال رمز الدعوة أو فتح رابط الدعوة."})
        return attrs


class JoinPreviewResponseSerializer(serializers.Serializer):
    organization = serializers.DictField()
    classroom = serializers.DictField(allow_null=True)


class MyMembershipSerializer(serializers.Serializer):
    """What a learner sees about their own place in an organization."""

    organization = serializers.DictField()
    classroom = serializers.DictField(allow_null=True)
    status = serializers.CharField()
    joined_at = serializers.DateTimeField(allow_null=True)
