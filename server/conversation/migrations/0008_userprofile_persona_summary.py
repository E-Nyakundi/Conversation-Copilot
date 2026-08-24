from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversation", "0007_userprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="persona_summary",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Distilled voice/personality summary built from the knowledge base. "
                    "Injected into every prompt alongside identity context, unlike knowledge "
                    "retrieval which is relevance-gated per turn."
                ),
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="persona_summary_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
