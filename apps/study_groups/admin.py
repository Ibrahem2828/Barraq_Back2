from django.contrib import admin

from .models import (
    GroupBan,
    GroupGoal,
    GroupMembership,
    GroupMessage,
    GroupSession,
    GroupTask,
    MessageReport,
    StudyGroup,
)


@admin.register(StudyGroup)
class StudyGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'invite_code', 'created_by', 'is_active', 'created_at')
    search_fields = ('name', 'invite_code')


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'role', 'status', 'joined_at')
    list_filter = ('role', 'status')


@admin.register(GroupBan)
class GroupBanAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'banned_by', 'created_at')


@admin.register(GroupGoal)
class GroupGoalAdmin(admin.ModelAdmin):
    list_display = ('title', 'group', 'is_done', 'target_date')


@admin.register(GroupTask)
class GroupTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'group', 'is_done', 'assigned_to')


@admin.register(GroupSession)
class GroupSessionAdmin(admin.ModelAdmin):
    list_display = ('group', 'scheduled_at', 'status', 'duration_minutes')
    list_filter = ('status',)


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'is_flagged', 'created_at')
    list_filter = ('is_flagged',)


@admin.register(MessageReport)
class MessageReportAdmin(admin.ModelAdmin):
    list_display = ('message', 'reported_by', 'created_at')
