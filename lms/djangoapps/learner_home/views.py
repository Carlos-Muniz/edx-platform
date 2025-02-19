"""
Views for the learner dashboard.
"""
from django.conf import settings
from edx_django_utils import monitoring as monitoring_utils
from opaque_keys.edx.keys import CourseKey
from rest_framework.response import Response
from rest_framework.generics import RetrieveAPIView

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.edxmako.shortcuts import marketing_link
from common.djangoapps.student.helpers import cert_info, get_resume_urls_for_enrollments
from common.djangoapps.student.views.dashboard import (
    complete_course_mode_info,
    get_course_enrollments,
    get_org_black_and_whitelist_for_site,
    get_filtered_course_entitlements,
)
from common.djangoapps.util.milestones_helpers import (
    get_pre_requisite_courses_not_completed,
)
from lms.djangoapps.bulk_email.models import Optout
from lms.djangoapps.bulk_email.models_api import is_bulk_email_feature_enabled
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.courseware.access import administrative_accesses_to_course_for_user
from lms.djangoapps.courseware.access_utils import (
    check_course_open_for_learner,
)
from lms.djangoapps.learner_home.serializers import LearnerDashboardSerializer
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.enterprise_support.api import (
    enterprise_customer_from_session_or_learner_data,
)


def get_platform_settings():
    """Get settings used for platform level connections: emails, url routes, etc."""

    return {
        "supportEmail": settings.DEFAULT_FEEDBACK_EMAIL,
        "billingEmail": settings.PAYMENT_SUPPORT_EMAIL,
        "courseSearchUrl": marketing_link("COURSES"),
    }


def get_user_account_confirmation_info(user):
    """Determine if a user needs to verify their account and related URL info"""

    activation_email_support_link = (
        configuration_helpers.get_value(
            "ACTIVATION_EMAIL_SUPPORT_LINK", settings.ACTIVATION_EMAIL_SUPPORT_LINK
        )
        or settings.SUPPORT_SITE_LINK
    )

    email_confirmation = {
        "isNeeded": not user.is_active,
        "sendEmailUrl": activation_email_support_link,
    }

    return email_confirmation


def get_enrollments(user, org_allow_list, org_block_list, course_limit=None):
    """Get enrollments and enrollment course modes for user"""

    course_enrollments = list(
        get_course_enrollments(user, org_allow_list, org_block_list, course_limit)
    )

    # Sort the enrollments by enrollment date
    course_enrollments.sort(key=lambda x: x.created, reverse=True)

    # Record how many courses there are so that we can get a better
    # understanding of usage patterns on prod.
    monitoring_utils.accumulate("num_courses", len(course_enrollments))

    # Retrieve the course modes for each course
    enrolled_course_ids = [enrollment.course_id for enrollment in course_enrollments]
    __, unexpired_course_modes = CourseMode.all_and_unexpired_modes_for_courses(
        enrolled_course_ids
    )
    course_modes_by_course = {
        course_id: {mode.slug: mode for mode in modes}
        for course_id, modes in unexpired_course_modes.items()
    }

    # Construct a dictionary of course mode information
    # used to render the course list.  We re-use the course modes dict
    # we loaded earlier to avoid hitting the database.
    course_mode_info = {
        enrollment.course_id: complete_course_mode_info(
            enrollment.course_id,
            enrollment,
            modes=course_modes_by_course[enrollment.course_id],
        )
        for enrollment in course_enrollments
    }

    return course_enrollments, course_mode_info


def get_entitlements(user, org_allow_list, org_block_list):
    """Get entitlements for the user"""
    (
        filtered_entitlements,
        course_entitlement_available_sessions,
        unfulfilled_entitlement_pseudo_sessions,
    ) = get_filtered_course_entitlements(user, org_allow_list, org_block_list)
    fulfilled_entitlements_by_course_key = {}
    unfulfilled_entitlements = []

    for course_entitlement in filtered_entitlements:
        if course_entitlement.enrollment_course_run:
            course_id = str(course_entitlement.enrollment_course_run.course.id)
            fulfilled_entitlements_by_course_key[course_id] = course_entitlement
        else:
            unfulfilled_entitlements.append(course_entitlement)

    return (
        fulfilled_entitlements_by_course_key,
        unfulfilled_entitlements,
        course_entitlement_available_sessions,
        unfulfilled_entitlement_pseudo_sessions,
    )


def get_course_overviews_for_pseudo_sessions(unfulfilled_entitlement_pseudo_sessions):
    """
    Get course overviews for entitlement pseudo sessions. This is required for
    serializing course providers for entitlements.

    Returns: dict of course overviews, keyed by CourseKey
    """
    course_ids = []

    # Get course IDs from unfulfilled entitlement pseudo sessions
    for pseudo_session in unfulfilled_entitlement_pseudo_sessions.values():
        course_id = pseudo_session.get("key")
        if course_id:
            course_ids.append(CourseKey.from_string(course_id))

    return CourseOverview.get_from_ids(course_ids)


def get_email_settings_info(user, course_enrollments):
    """
    Given a user and enrollments, determine which courses allow bulk email (show_email_settings_for)
    and which the learner has opted out from (optouts)
    """
    course_optouts = Optout.objects.filter(user=user).values_list(
        "course_id", flat=True
    )

    # only show email settings for course where bulk email is turned on
    show_email_settings_for = frozenset(
        enrollment.course_id
        for enrollment in course_enrollments
        if (is_bulk_email_feature_enabled(enrollment.course_id))
    )

    return show_email_settings_for, course_optouts


def get_ecommerce_payment_page(user):
    """Determine the ecommerce payment page URL if enabled for this user"""
    ecommerce_service = EcommerceService()
    return (
        ecommerce_service.payment_page_url()
        if ecommerce_service.is_enabled(user)
        else None
    )


def get_cert_statuses(user, course_enrollments):
    """Get cert status by course for user enrollments"""
    return {
        enrollment.course_id: cert_info(user, enrollment)
        for enrollment in course_enrollments
    }


def _get_courses_with_unmet_prerequisites(user, course_enrollments):
    """
    Determine which courses have unmet prerequisites.
    NOTE: that courses w/out prerequisites, or with met prerequisites are not returned
    in the output dict. That way we can do a simple "course_id in dict" check.

    Returns: {
        <course_id>: { "courses": [listing of unmet prerequisites] }
    }
    """

    courses_having_prerequisites = frozenset(
        enrollment.course_id
        for enrollment in course_enrollments
        if enrollment.course_overview.pre_requisite_courses
    )

    return get_pre_requisite_courses_not_completed(user, courses_having_prerequisites)


def check_course_access(user, course_enrollments):
    """
    Wrapper for checks surrounding user ability to view courseware

    Returns: {
        <course_enrollment.id>: {
            "has_unmet_prerequisites": True/False,
            "is_too_early_to_view": True/False,
            "user_has_staff_access": True/False
        }
    }
    """

    course_access_dict = {}

    courses_with_unmet_prerequisites = _get_courses_with_unmet_prerequisites(
        user, course_enrollments
    )

    for course_enrollment in course_enrollments:
        course_access_dict[course_enrollment.course_id] = {
            "has_unmet_prerequisites": course_enrollment.course_id
            in courses_with_unmet_prerequisites,
            "is_too_early_to_view": not check_course_open_for_learner(
                user, course_enrollment.course
            ),
            "user_has_staff_access": any(
                administrative_accesses_to_course_for_user(
                    user, course_enrollment.course_id
                )
            ),
        }

    return course_access_dict


class InitializeView(RetrieveAPIView):  # pylint: disable=unused-argument
    """List of courses a user is enrolled in or entitled to"""

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        # Get user, determine if user needs to confirm email account
        user = request.user
        email_confirmation = get_user_account_confirmation_info(user)

        # Gather info for enterprise dashboard
        enterprise_customer = enterprise_customer_from_session_or_learner_data(request)

        # Get the org whitelist or the org blacklist for the current site
        site_org_whitelist, site_org_blacklist = get_org_black_and_whitelist_for_site()

        # Get entitlements and course overviews for serializing
        (
            fulfilled_entitlements_by_course_key,
            unfulfilled_entitlements,
            course_entitlement_available_sessions,
            unfulfilled_entitlement_pseudo_sessions,
        ) = get_entitlements(user, site_org_whitelist, site_org_blacklist)
        pseudo_session_course_overviews = get_course_overviews_for_pseudo_sessions(
            unfulfilled_entitlement_pseudo_sessions
        )

        # Get enrollments
        course_enrollments, course_mode_info = get_enrollments(
            user, site_org_whitelist, site_org_blacklist
        )

        # Get email opt-outs for student
        show_email_settings_for, course_optouts = get_email_settings_info(
            user, course_enrollments
        )

        # Get cert status by course
        cert_statuses = get_cert_statuses(user, course_enrollments)

        # Determine view access for course, (for showing courseware link) involves:
        course_access_checks = check_course_access(user, course_enrollments)

        # TODO - Get related programs

        # e-commerce info
        ecommerce_payment_page = get_ecommerce_payment_page(user)

        # Gather urls for course card resume buttons.
        resume_button_urls = get_resume_urls_for_enrollments(user, course_enrollments)

        learner_dash_data = {
            "emailConfirmation": email_confirmation,
            "enterpriseDashboard": enterprise_customer,
            "platformSettings": get_platform_settings(),
            "enrollments": course_enrollments,
            "unfulfilledEntitlements": unfulfilled_entitlements,
            "suggestedCourses": [],
        }

        context = {
            "ecommerce_payment_page": ecommerce_payment_page,
            "cert_statuses": cert_statuses,
            "course_mode_info": course_mode_info,
            "course_optouts": course_optouts,
            "course_access_checks": course_access_checks,
            "resume_course_urls": resume_button_urls,
            "show_email_settings_for": show_email_settings_for,
            "fulfilled_entitlements": fulfilled_entitlements_by_course_key,
            "course_entitlement_available_sessions": course_entitlement_available_sessions,
            "unfulfilled_entitlement_pseudo_sessions": unfulfilled_entitlement_pseudo_sessions,
            "pseudo_session_course_overviews": pseudo_session_course_overviews,
        }

        response_data = LearnerDashboardSerializer(
            learner_dash_data, context=context
        ).data
        return Response(response_data)
