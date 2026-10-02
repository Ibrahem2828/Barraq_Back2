from django.contrib import admin

from .models import HelpfulVote, KnowledgeReply, KnowledgeThread, SavedThread


@admin.register(KnowledgeThread)
class KnowledgeThreadAdmin(admin.ModelAdmin):
    list_display = ('title', 'classroom', 'author', 'thread_type', 'status', 'created_at')
    list_filter = ('thread_type', 'status')
    search_fields = ('title', 'body')


@admin.register(KnowledgeReply)
class KnowledgeReplyAdmin(admin.ModelAdmin):
    list_display = ('thread', 'author', 'is_teacher_reply', 'helpful_count', 'created_at')
    list_filter = ('is_teacher_reply',)


@admin.register(HelpfulVote)
class HelpfulVoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'reply', 'created_at')


@admin.register(SavedThread)
class SavedThreadAdmin(admin.ModelAdmin):
    list_display = ('user', 'thread', 'created_at')
