"""
Tests for discussions tasks.
"""
import ddt
import mock


from openedx_events.learning.data import DiscussionTopicContext
from openedx.core.djangoapps.discussions.tasks import update_discussions_settings_from_course

from xmodule.modulestore.tests.django_utils import TEST_DATA_MONGO_AMNESTY_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory


@ddt.ddt
@mock.patch('openedx.core.djangoapps.discussions.tasks.DiscussionsConfiguration', mock.Mock())
class UpdateDiscussionsSettingsFromCourseTestCase(ModuleStoreTestCase):
    """
    Tests for the discussions settings update tasks
    """
    MODULESTORE = TEST_DATA_MONGO_AMNESTY_MODULESTORE

    def setUp(self):
        super().setUp()
        self.course = course = CourseFactory.create()
        self.course_key = course_key = self.course.id
        with self.store.bulk_operations(course_key):
            self.section = ItemFactory.create(
                parent_location=course.location,
                category="chapter",
                display_name="Section"
            )
            self.sequence = ItemFactory.create(
                parent_location=self.section.location,
                category="sequential",
                display_name="Sequence"
            )
            self.unit = ItemFactory.create(
                parent_location=self.sequence.location,
                category="vertical",
                display_name="Unit"
            )
            ItemFactory.create(
                parent_location=self.sequence.location,
                category="vertical",
                display_name="Discussable Unit",
                discussion_enabled=True,
            )
            ItemFactory.create(
                parent_location=self.sequence.location,
                category="vertical",
                display_name="Non-Discussable Unit",
                discussion_enabled=False,
            )
            ItemFactory.create(
                parent_location=self.unit.location,
                category="html",
                display_name="An HTML Module"
            )
            graded_sequence = ItemFactory.create(
                parent_location=self.section.location,
                category="sequential",
                display_name="Graded Sequence",
                graded=True,
            )
            graded_unit = ItemFactory.create(
                parent_location=graded_sequence.location,
                category="vertical",
                display_name="Graded Unit"
            )
            ItemFactory.create(
                parent_location=graded_sequence.location,
                category="vertical",
                display_name="Discussable Graded Unit",
                discussion_enabled=True,
            )
            ItemFactory.create(
                parent_location=graded_sequence.location,
                category="vertical",
                display_name="Non-Discussable Graded Unit",
                discussion_enabled=False,
            )
            ItemFactory.create(
                parent_location=graded_unit.location,
                category="html",
                display_name="Graded HTML Module"
            )

    def update_course_field(self, **update):
        """
        Update the test course using provided parameters.
        """
        for key, value in update.items():
            setattr(self.course, key, value)
        self.update_course(self.course, self.user.id)

    def update_discussions_settings(self, settings):
        """
        Update course discussion settings based on the provided discussion settings.
        """
        self.course.discussions_settings.update(settings)
        self.update_course(self.course, self.user.id)

    def test_default(self):
        """
        Test that the course defaults.
        """
        config_data = update_discussions_settings_from_course(self.course.id)
        assert config_data.course_key == self.course.id
        assert config_data.enable_graded_units is False
        assert config_data.unit_level_visibility is True
        assert config_data.provider_type is not None
        assert config_data.plugin_configuration == {}
        assert {context.title for context in config_data.contexts} == {"General", "Unit", "Discussable Unit"}

    def test_topics_contexts(self):
        """
        Test the handling of topics.
        """
        self.update_course_field(discussion_topics={
            "General": {"id": "general-topic"},
            "Test Topic": {"id": "test-topic"},
        })
        config_data = update_discussions_settings_from_course(self.course.id)
        assert len(config_data.contexts) == 4
        assert DiscussionTopicContext(
            title="General",
            external_id="general-topic",
            ordering=0,
        ) in config_data.contexts
        assert DiscussionTopicContext(
            title="Test Topic",
            external_id="test-topic",
            ordering=1,
        ) in config_data.contexts
        assert DiscussionTopicContext(
            title='Unit',
            usage_key=self.unit.location,
            group_id=None,
            external_id=None,
            ordering=100,
            context={'section': 'Section', 'subsection': 'Sequence', 'unit': 'Unit'}
        ) in config_data.contexts

    @ddt.data(
        ({}, 3, {"Unit", "Discussable Unit"},
         {"Graded Unit", "Non-Discussable Unit", "Discussable Graded Unit", "Non-Discussable Graded Unit"}),
        ({"enable_in_context": False}, 1, set(), {"Unit", "Graded Unit"}),
        ({"unit_level_visibility": False, "enable_graded_units": False}, 4,
         {"Unit", "Discussable Unit", "Non-Discussable Unit"},
         {"Graded Unit"}),
        ({"unit_level_visibility": False, "enable_graded_units": True}, 7,
         {"Unit", "Graded Unit", "Discussable Graded Unit"}, set()),
        ({"enable_graded_units": True}, 5,
         {"Discussable Unit", "Discussable Graded Unit", "Graded Unit"},
         {"Non-Discussable Unit", "Non-Discussable Graded Unit"}),
    )
    @ddt.unpack
    def test_custom_discussion_settings(self, settings, context_count, present_units, missing_units):
        """
        Test different combinations of settings and their impact on the units that are returned.
        """
        self.update_discussions_settings(settings)
        config_data = update_discussions_settings_from_course(self.course.id)
        assert len(config_data.contexts) == context_count
        units_in_config = {context.title for context in config_data.contexts}
        assert present_units <= units_in_config
        assert not missing_units & units_in_config
