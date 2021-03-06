import uuid

from unittest import mock

from olympia import amo, core
from olympia.activity.models import ActivityLog
from olympia.addons.models import Addon
from olympia.amo.tests import TestCase, addon_factory, collection_factory
from olympia.bandwagon.models import Collection, FeaturedCollection
from olympia.users.models import UserProfile


def get_addons(c):
    q = c.addons.order_by('collectionaddon__ordering')
    return list(q.values_list('id', flat=True))


def activitylog_count(type):
    qs = ActivityLog.objects
    if type:
        qs = qs.filter(action=type.id)
    return qs.count()


class TestCollections(TestCase):
    fixtures = ('base/addon_3615', 'bandwagon/test_models',
                'base/user_4043307')

    def setUp(self):
        super(TestCollections, self).setUp()
        self.user = UserProfile.objects.create(username='uhhh', email='uh@hh')
        self.other = UserProfile.objects.exclude(id=self.user.id)[0]
        core.set_user(self.user)

    def test_description(self):
        c = Collection.objects.create(
            description='<a href="http://example.com">example.com</a> '
                        'http://example.com <b>foo</b> some text')
        # All markup escaped, links are stripped.
        assert str(c.description) == '&lt;b&gt;foo&lt;/b&gt; some text'

    def test_translation_default(self):
        """Make sure we're getting strings from the default locale."""
        c = Collection.objects.get(pk=512)
        assert str(c.name) == 'yay'

    def test_listed(self):
        """Make sure the manager's listed() filter works."""
        listed_count = Collection.objects.listed().count()
        # Make a private collection.
        Collection.objects.create(
            name='Hello', uuid='4e2a1acc39ae47ec956f46e080ac7f69',
            listed=False, author=self.user)

        assert Collection.objects.listed().count() == listed_count

    def test_auto_uuid(self):
        c = Collection.objects.create(author=self.user)
        assert c.uuid
        assert isinstance(c.uuid, uuid.UUID)

    def test_collection_meta(self):
        c = Collection.objects.create(author=self.user)
        assert c.addon_count == 0
        c.add_addon(Addon.objects.all()[0])
        assert activitylog_count(amo.LOG.ADD_TO_COLLECTION) == 1
        c = Collection.objects.get(id=c.id)
        assert c.addon_count == 1

    def test_favorites_slug(self):
        c = Collection.objects.create(author=self.user, slug='favorites')
        assert c.type == amo.COLLECTION_NORMAL
        assert c.slug == 'favorites~'

        c = Collection.objects.create(author=self.user, slug='favorites')
        assert c.type == amo.COLLECTION_NORMAL
        assert c.slug == 'favorites~-1'

    def test_slug_dupe(self):
        c = Collection.objects.create(author=self.user, slug='boom')
        assert c.slug == 'boom'
        c.save()
        assert c.slug == 'boom'
        c = Collection.objects.create(author=self.user, slug='boom')
        assert c.slug == 'boom-1'
        c = Collection.objects.create(author=self.user, slug='boom')
        assert c.slug == 'boom-2'


class TestCollectionQuerySet(TestCase):
    fixtures = ('base/addon_3615',)

    def test_with_has_addon(self):
        user = UserProfile.objects.create(username='uhhh', email='uh@hh')
        collection = Collection.objects.create(author=user)
        addon = Addon.objects.all()[0]

        qset = (
            Collection.objects
            .filter(pk=collection.id)
            .with_has_addon(addon.id))

        assert not qset.first().has_addon

        collection.add_addon(addon)

        assert qset.first().has_addon


class TestFeaturedCollectionSignals(TestCase):
    """The signal needs to fire for all cases when Addon.is_featured would
    potentially change."""
    MOCK_TARGET = 'olympia.bandwagon.models.Collection.update_featured_status'

    def setUp(self):
        super(TestFeaturedCollectionSignals, self).setUp()
        self.collection = collection_factory()
        self.addon = addon_factory()
        self.collection.add_addon(self.addon)

    def test_update_featured_status_does_index_addons(self):
        from olympia.addons.tasks import index_addons

        extra_addon = addon_factory()

        # Make sure index_addons is a mock, and then clear it.
        assert index_addons.delay.call_count
        index_addons.delay.reset_mock()

        # Featuring the collection indexes the add-ons in it.
        FeaturedCollection.objects.create(
            collection=self.collection,
            application=self.collection.application)
        assert index_addons.delay.call_count == 1
        assert index_addons.delay.call_args[0] == ([self.addon.pk],)
        index_addons.delay.reset_mock()

        # Adding an add-on re-indexes all add-ons in the collection
        # (we're not smart enough to know it's only necessary to do it for
        # the one we just added and not the rest).
        self.collection.add_addon(extra_addon)
        assert index_addons.delay.call_count == 1
        assert index_addons.delay.call_args[0] == (
            [self.addon.pk, extra_addon.pk],)
        index_addons.delay.reset_mock()

        # Removing an add-on needs just reindexes the add-on that has been
        # removed.
        self.collection.remove_addon(extra_addon)
        assert index_addons.delay.call_count == 1
        assert index_addons.delay.call_args_list[0][0] == ([extra_addon.pk],)

    def test_addon_added_to_featured_collection(self):
        FeaturedCollection.objects.create(
            collection=self.collection,
            application=self.collection.application)

        with mock.patch(self.MOCK_TARGET) as function_mock:
            self.collection.add_addon(addon_factory())
            function_mock.assert_called()

    def test_addon_removed_from_featured_collection(self):
        addon = addon_factory()
        self.collection.add_addon(addon)
        FeaturedCollection.objects.create(
            collection=self.collection,
            application=self.collection.application)

        with mock.patch(self.MOCK_TARGET) as function_mock:
            self.collection.remove_addon(addon)
            function_mock.assert_called()

    def test_featured_collection_deleted(self):
        FeaturedCollection.objects.create(
            collection=self.collection,
            application=self.collection.application)

        with mock.patch(self.MOCK_TARGET) as function_mock:
            self.collection.delete()
            function_mock.assert_called()

    def test_collection_becomes_featured(self):
        with mock.patch(self.MOCK_TARGET) as function_mock:
            FeaturedCollection.objects.create(
                collection=self.collection,
                application=self.collection.application)
            function_mock.assert_called()

    def test_collection_stops_being_featured(self):
        featured = FeaturedCollection.objects.create(
            collection=self.collection,
            application=self.collection.application)

        with mock.patch(self.MOCK_TARGET) as function_mock:
            featured.delete()
            function_mock.assert_called()

    def test_signal_only_with_featured(self):
        with mock.patch(self.MOCK_TARGET) as function_mock:
            addon = addon_factory()
            collection = collection_factory()
            collection.add_addon(addon)
            collection.remove_addon(addon)
            collection.delete()
            function_mock.assert_not_called()
