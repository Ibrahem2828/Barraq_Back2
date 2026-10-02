from django.contrib import admin

from .models import (
    ChallengeProgress,
    ClassGoal,
    PathStation,
    StationCompletion,
    StudentJourneyProgress,
    SubjectPath,
    Unlockable,
    UnlockedItem,
    WeeklyChallenge,
)


class PathStationInline(admin.TabularInline):
    model = PathStation
    extra = 0


@admin.register(SubjectPath)
class SubjectPathAdmin(admin.ModelAdmin):
    list_display = ('subject', 'is_active')
    inlines = [PathStationInline]


@admin.register(StudentJourneyProgress)
class StudentJourneyProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject', 'gem_fragments_total', 'current_station')


@admin.register(StationCompletion)
class StationCompletionAdmin(admin.ModelAdmin):
    list_display = ('user', 'station', 'created_at')


@admin.register(Unlockable)
class UnlockableAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'gem_requirement', 'is_active')
    list_filter = ('category', 'is_active')


@admin.register(UnlockedItem)
class UnlockedItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'unlockable', 'created_at')


@admin.register(WeeklyChallenge)
class WeeklyChallengeAdmin(admin.ModelAdmin):
    list_display = ('title', 'week_start', 'target_value', 'gem_reward', 'is_active')
    list_filter = ('week_start', 'is_active')


@admin.register(ChallengeProgress)
class ChallengeProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'challenge', 'progress_value', 'is_completed')


@admin.register(ClassGoal)
class ClassGoalAdmin(admin.ModelAdmin):
    list_display = ('title', 'classroom', 'progress_value', 'target_value', 'is_completed')
