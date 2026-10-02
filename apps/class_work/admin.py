from django.contrib import admin

from .models import (
    AssignmentSubmission,
    ClassAnnouncement,
    ClassAssignment,
    ClassEvent,
    ClassQuiz,
    ClassQuizAttempt,
    ClassQuizQuestion,
)


@admin.register(ClassAnnouncement)
class ClassAnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'classroom', 'pinned', 'created_at')
    list_filter = ('pinned',)
    search_fields = ('title', 'body')


@admin.register(ClassEvent)
class ClassEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'classroom', 'event_type', 'start_at')
    list_filter = ('event_type',)


@admin.register(ClassAssignment)
class ClassAssignmentAdmin(admin.ModelAdmin):
    list_display = ('title', 'classroom', 'status', 'due_at')
    list_filter = ('status',)


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = ('assignment', 'student', 'status', 'grade', 'submitted_at')
    list_filter = ('status',)


class ClassQuizQuestionInline(admin.TabularInline):
    model = ClassQuizQuestion
    extra = 0


@admin.register(ClassQuiz)
class ClassQuizAdmin(admin.ModelAdmin):
    list_display = ('title', 'classroom', 'status', 'created_at')
    list_filter = ('status',)
    inlines = [ClassQuizQuestionInline]


@admin.register(ClassQuizAttempt)
class ClassQuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('student', 'class_quiz', 'status', 'score', 'max_score', 'fully_graded')
    list_filter = ('status', 'fully_graded')
