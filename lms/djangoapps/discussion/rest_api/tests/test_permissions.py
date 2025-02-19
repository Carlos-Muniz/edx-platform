"""
Tests for discussion API permission logic
"""


import itertools

import ddt
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from lms.djangoapps.discussion.rest_api.permissions import (
    can_delete,
    get_editable_fields,
    get_initializable_comment_fields,
    get_initializable_thread_fields
)
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.comment_client.user import User


def _get_context(
    requester_id,
    is_requester_privileged,
    is_cohorted=False,
    thread=None,
    allow_anonymous=True,
    allow_anonymous_to_peers=False,
):
    """Return a context suitable for testing the permissions module"""
    return {
        "cc_requester": User(id=requester_id),
        "is_requester_privileged": is_requester_privileged,
        "course": CourseFactory(
            cohort_config={"cohorted": is_cohorted},
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
        ),
        "discussion_division_enabled": is_cohorted,
        "thread": thread,
    }


@ddt.ddt
class GetInitializableFieldsTest(ModuleStoreTestCase):
    """Tests for get_*_initializable_fields"""
    @ddt.data(*itertools.product(*[[True, False] for _ in range(4)]))
    @ddt.unpack
    def test_thread(
        self,
        is_privileged,
        is_cohorted,
        allow_anonymous,
        allow_anonymous_to_peers,
    ):
        context = _get_context(
            requester_id="5",
            is_requester_privileged=is_privileged,
            is_cohorted=is_cohorted,
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
        )
        actual = get_initializable_thread_fields(context)
        expected = {
            "abuse_flagged", "copy_link", "course_id", "following", "raw_body",
            "read", "title", "topic_id", "type", "voted"
        }
        if is_privileged:
            expected |= {"closed", "pinned", "close_reason_code", "edit_reason_code"}
        if is_privileged and is_cohorted:
            expected |= {"group_id"}
        if allow_anonymous:
            expected |= {"anonymous"}
        if allow_anonymous_to_peers:
            expected |= {"anonymous_to_peers"}
        assert actual == expected

    @ddt.data(*itertools.product([True, False], ["question", "discussion"], [True, False]))
    @ddt.unpack
    def test_comment(self, is_thread_author, thread_type, is_privileged):
        context = _get_context(
            requester_id="5",
            is_requester_privileged=is_privileged,
            thread=Thread(user_id="5" if is_thread_author else "6", thread_type=thread_type)
        )
        actual = get_initializable_comment_fields(context)
        expected = {
            "anonymous", "abuse_flagged", "parent_id", "raw_body", "thread_id", "voted"
        }
        if is_privileged:
            expected |= {"edit_reason_code"}
        if (is_thread_author and thread_type == "question") or is_privileged:
            expected |= {"endorsed"}
        assert actual == expected


@ddt.ddt
class GetEditableFieldsTest(ModuleStoreTestCase):
    """Tests for get_editable_fields"""
    @ddt.data(*itertools.product(*[[True, False] for _ in range(5)]))
    @ddt.unpack
    def test_thread(
        self,
        is_author,
        is_privileged,
        is_cohorted,
        allow_anonymous,
        allow_anonymous_to_peers
    ):
        thread = Thread(user_id="5" if is_author else "6", type="thread")
        context = _get_context(
            requester_id="5",
            is_requester_privileged=is_privileged,
            is_cohorted=is_cohorted,
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
        )
        actual = get_editable_fields(thread, context)
        expected = {"abuse_flagged", "copy_link", "following", "read", "voted"}
        if is_privileged:
            expected |= {"closed", "pinned", "close_reason_code", "edit_reason_code"}
        if is_author or is_privileged:
            expected |= {"topic_id", "type", "title", "raw_body"}
        if is_privileged and is_cohorted:
            expected |= {"group_id"}
        if is_author and allow_anonymous:
            expected |= {"anonymous"}
        if is_author and allow_anonymous_to_peers:
            expected |= {"anonymous_to_peers"}
        assert actual == expected

    @ddt.data(*itertools.product(*[[True, False] for _ in range(6)], ["question", "discussion"]))
    @ddt.unpack
    def test_comment(
        self,
        is_author,
        is_thread_author,
        is_privileged,
        allow_anonymous,
        allow_anonymous_to_peers,
        has_parent,
        thread_type
    ):
        comment = Comment(
            user_id="5" if is_author else "6",
            type="comment",
            parent_id="parent-id" if has_parent else None,
        )
        context = _get_context(
            requester_id="5",
            is_requester_privileged=is_privileged,
            thread=Thread(user_id="5" if is_thread_author else "6", thread_type=thread_type),
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
        )
        actual = get_editable_fields(comment, context)
        expected = {"abuse_flagged", "voted"}
        if is_privileged:
            expected |= {"edit_reason_code"}
        if is_author or is_privileged:
            expected |= {"raw_body"}
        if not has_parent and ((is_thread_author and thread_type == "question") or is_privileged):
            expected |= {"endorsed"}
        if is_author and allow_anonymous:
            expected |= {"anonymous"}
        if is_author and allow_anonymous_to_peers:
            expected |= {"anonymous_to_peers"}
        assert actual == expected


@ddt.ddt
class CanDeleteTest(ModuleStoreTestCase):
    """Tests for can_delete"""
    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    def test_thread(self, is_author, is_privileged):
        thread = Thread(user_id="5" if is_author else "6")
        context = _get_context(requester_id="5", is_requester_privileged=is_privileged)
        assert can_delete(thread, context) == (is_author or is_privileged)

    @ddt.data(*itertools.product([True, False], [True, False], [True, False]))
    @ddt.unpack
    def test_comment(self, is_author, is_thread_author, is_privileged):
        comment = Comment(user_id="5" if is_author else "6")
        context = _get_context(
            requester_id="5",
            is_requester_privileged=is_privileged,
            thread=Thread(user_id="5" if is_thread_author else "6")
        )
        assert can_delete(comment, context) == (is_author or is_privileged)
