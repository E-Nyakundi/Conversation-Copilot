# Session-cap fix (2026-09-01): the account-wide minutes pool
# (TenantQuota.max_minutes/minutes_used) is removed outright per Lewis's
# call — it's being replaced by a per-session, resetting 2-hour hard cap
# (see conversation.models.Session), not by anything tracked on this
# model. max_sessions/sessions_used are untouched.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='tenantquota',
            name='max_minutes',
        ),
        migrations.RemoveField(
            model_name='tenantquota',
            name='minutes_used',
        ),
    ]
