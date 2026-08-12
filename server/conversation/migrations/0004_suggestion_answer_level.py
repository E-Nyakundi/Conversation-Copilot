# Phase 4: answer-level switching (Quick / Medium / Detailed) — see
# ai/prompts.py:ANSWER_LEVELS and conversation/models.py:Suggestion.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversation', '0003_suggestion'),
    ]

    operations = [
        migrations.AddField(
            model_name='suggestion',
            name='answer_level',
            field=models.CharField(
                choices=[('quick', 'Quick'), ('medium', 'Medium'), ('detailed', 'Detailed')],
                default='medium',
                max_length=20,
            ),
        ),
    ]
