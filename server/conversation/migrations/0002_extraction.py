import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversation", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Extraction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("question", "Question"),
                            ("decision", "Decision"),
                            ("request", "Request"),
                            ("objection", "Objection"),
                            ("action_item", "Action item"),
                            ("topic_change", "Topic change"),
                        ],
                        max_length=20,
                    ),
                ),
                ("text", models.TextField()),
                ("confidence", models.FloatField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extractions",
                        to="conversation.session",
                    ),
                ),
                (
                    "turn",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extractions",
                        to="conversation.turn",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
