# recruitment/utils.py
from django.core import signing
from django.urls import reverse
from django.conf import settings

def generate_salted_apply_url(job_ad_id):
    token = signing.dumps({'job_ad_id': job_ad_id}, salt='tutor-application-salt')
    path = reverse('recruitment:tutor_apply', kwargs={'token': token})
    return f"{settings.SITE_DOMAIN}{path}"

def decode_apply_token(token):
    # Valid for 7 days (604800 seconds)
    return signing.loads(token, salt='tutor-application-salt', max_age=604800)

def generate_salted_report_url(application_id):
    token = signing.dumps({'application_id': application_id}, salt='tutor-report-salt')
    path = reverse('recruitment:submit_report', kwargs={'token': token})
    return f"{settings.SITE_DOMAIN}{path}"

def decode_report_token(token):
    return signing.loads(token, salt='tutor-report-salt', max_age=1209600)
