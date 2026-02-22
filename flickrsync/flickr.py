import flickrapi
import os
import re
import time
import logging
from flickrsync import general
from flickrsync.error import Error
from flickrsync.log import Log

logger = logging.getLogger(Log.NAME)

class Flickr:
    PER_PAGE = 500
    FLICKR_PERMS = 'write'
    PHOTOSET_DESCRIPTION = '[created by %s]' % general.APPLICATION_NAME
    MACHINE_TAG_SIGNATURE = '%s:signature' % general.APPLICATION_NAME

    def __init__(self, api_key, api_secret, username):
        assert api_key, "api_key not supplied<%s>" % api_key
        assert api_secret, "api_secret not supplied<%s>" % api_secret
        assert username, "username not supplied<%s>" % username

        try:
            self.api = flickrapi.FlickrAPI(api_key, api_secret, username = username)
        except flickrapi.exceptions.FlickrError as e:
            raise Error(e)

        logger.debug('%s' % self.api)

    def get_photosets(self):
        photosets = []

        try:
            for photoset in self.api.walk_photosets():
                a_set = {
                      'id'           : photoset.get('id')
                    , 'datecreate'   : photoset.get('date_create')
                    , 'dateupdate'   : photoset.get('date_update')
                    , 'title'        : photoset.find('title').text
                    , 'description'  : photoset.find('description').text
                    }
                photosets.append(a_set)
                logger.debug('[%d] %s' % (len(photosets), a_set))

        except flickrapi.exceptions.FlickrError as e:
            raise Error(e)

        return photosets

    def get_photos(self, minuploaddate):
        assert minuploaddate >= 0, "minuploaddate not supplied<%s>" % minuploaddate
        logger.debug("minuploaddate<%s>" % minuploaddate)

        photos = []

        try:
            for photo in self.api.walk_user(per_page = Flickr.PER_PAGE, min_upload_date = minuploaddate,
                                          extras = "date_upload,date_taken,original_format,tags,machine_tags,url_o"):
                try :
                    a_photo = {
                          'id'                   : photo.get('id')
                        , 'title'                : photo.get('title')
                        , 'originalformat'       : photo.get('originalformat')
                        , 'dateupload'           : photo.get('dateupload')
                        , 'datetaken'            : photo.get('datetaken')
                        , 'datetakenunknown'     : photo.get('datetakenunknown')
                        , 'datetakengranularity' : photo.get('datetakengranularity')
                        , 'url_o'                : photo.get('url_o')
                        , 'originalsecret'       : photo.get('originalsecret')
                        , 'farm'                 : photo.get('farm')
                        , 'server'               : photo.get('server')
                        , 'tags'                 : photo.get('tags')
                        , 'machine_tags'         : photo.get('machine_tags')
                        , 'signature'            : self.__get_signature(photo.get('machine_tags'))
                        , 'shortname'            : self.__get_short_name(photo)
                        , 'dateflat'             : general.get_flat_date(photo.get('datetaken'))
                    }
                    photos.append(a_photo)
                    logger.debug('[%d] %s' % (len(photos), a_photo))
                except UnicodeError as e :
                    logger.error('Unicode Error: photo.id<%s>, error<%s>' % (photo.get('id'), e))

        except flickrapi.exceptions.FlickrError as e:
            raise Error(e)

        return photos

    def authenticate(self):
        try:
            # Only do this if we don't have a valid token already
            if not self.api.token_valid(perms = Flickr.FLICKR_PERMS):
                # Get a request token
                self.api.get_request_token(oauth_callback = 'oob')

                authorize_url = self.api.auth_url(perms = Flickr.FLICKR_PERMS)

                print('Open a browser at the authentication URL:')
                print(authorize_url)

                # Get the verifier code from the user. Do this however you
                # want, as long as the user gives the application the code.
                verifier = str(input('Enter the Flickr Verifier code: '))

                # Trade the request token for an access token
                self.api.get_access_token(verifier)

            resp = self.api.test.login()
            user0 = resp.find('user')

            print('authenticated id<%s>' % user0.attrib['id'])
            print('authenticated id<%s>' % user0.find('username').text)

        except flickrapi.exceptions.FlickrError as e:
            raise Error(e)

    def upload_photos(self, uploadphotos):
        passed = 0
        failed = 0

        tickets = []

        for aphoto in uploadphotos:
            pathname = os.path.join(aphoto['directory'], aphoto['filename'])
            tag = self.get_signature_tag(aphoto['signature'])
            title = general.get_title(aphoto['filename'])
            logger.info('{title}, {pathname}'.format(title=title, pathname=pathname))

            try:
                response = self.api.upload(filename=pathname, title=title, tags=tag, asynchronous=1, is_public=0, is_family=0, is_friend=0)
                tickets.append(response.find('ticketid').text)
                passed += 1
            except flickrapi.exceptions.FlickrError as e:
                logger.error(e)
                failed += 1

        logger.debug('tickets<%s>' % (tickets))

        self.__wait_until_uploading_complete(tickets)

        logger.debug('passed: <%d>, failed: <%d>' % (passed, failed))
        return passed, failed

    def photoset_create(self, photosetname, primaryphotoid):
        assert photosetname, 'photosetname<%s>' % photosetname
        assert primaryphotoid, 'primaryphotoid<%s>' % primaryphotoid

        try:
            newphotoset = self.api.photosets.create(title = photosetname, description = Flickr.PHOTOSET_DESCRIPTION, primary_photo_id = primaryphotoid)
        except flickrapi.exceptions.FlickrError as e:
            logger.error(e)

        photosetid = newphotoset.find('photoset').attrib['id']
        logger.info('created new photosetid<%s>, <%s>' % (photosetid, photosetname))
        return photosetid

    def photoset_edit(self, photosetid, primaryphotoid, photoscsv):
        assert photosetid, 'photosetid<%s>' % photosetid
        assert primaryphotoid, 'primaryphotoid<%s>' % primaryphotoid
        assert photoscsv, 'photoscsv<%s>' % photoscsv
        logger.debug('photosetid<%s>' % photosetid)
        logger.debug('primaryphotoid<%s>' % primaryphotoid)
        logger.debug('photoscsv<%s>' % photoscsv)

        try:
            self.api.photosets.editPhotos(photoset_id = photosetid, primary_photo_id = primaryphotoid, photo_ids = photoscsv)

        except flickrapi.exceptions.FlickrError as e:
            logger.error(e)

    def delete_unused_photosets(self, photosetsused, photosets):
        assert photosetsused, 'photosetsused<%s>' % photosetsused

        logger.debug('photosetsused<%s>' % photosetsused)
        logger.debug('photosets<%s>' % photosets)
        logger.debug('photosetdescription<%s>' % Flickr.PHOTOSET_DESCRIPTION)

        countdeleted = 0

        for photoset in photosets:
            logger.debug('photoset[id]<%s>' % photoset['id'])
            logger.debug('photoset[description]<%s>' % photoset['description'])

            # only delete photosets originally created by us (i.e. photoset description set by this application)
            # and if photoset is not being used
            if photoset['description'] == Flickr.PHOTOSET_DESCRIPTION and photoset['id'] not in photosetsused:

                try:
                    self.api.photosets.delete(photoset_id = photoset['id'])
                except flickrapi.exceptions.FlickrError as e:
                    logger.error(e)

                logger.info('deleted photoset[id]<%s>' % photoset['id'])
                countdeleted += 1

            else:
                logger.debug('keep photoset[id]<%s>' % photoset['id'])

        logger.info('photosets deleted<%d>' % countdeleted)
        return countdeleted

    def set_tags(self, photoid, tags):
        assert photoid, 'photoid missing'
        logger.info('Setting tags to Flickr photo<{photoid}>, <{tags}>'.format(photoid=photoid, tags=tags))
        try:
            self.api.photos.setTags(photo_id=photoid, tags=tags)
        except flickrapi.exceptions.FlickrError as e:
            logger.error(e)

    def add_tags(self, photoid, tags):
        assert photoid, 'photoid missing'
        assert tags, 'tags missing'
        logger.info('Adding tag to Flickr photo<{photoid}>, <{tags}'.format(photoid=photoid, tags=tags))
        try:
            self.api.photos.addTags(photo_id=photoid, tags=tags)
        except flickrapi.exceptions.FlickrError as e:
            logger.error(e)

    def __wait_until_uploading_complete(self, tickets):
        completed = False
        notcomplete = list(tickets)

        while not completed:
            response = self.api.photos.upload.checkTickets(tickets=general.list_to_csv(notcomplete))
            notcomplete.clear()

            for ticket in response.find('uploader'):
                logger.info('id<%s>, complete<%s>, invalid<%s>' % (ticket.get('id'), ticket.get('complete'), ticket.get('invalid')))

                if ticket.get('complete') == '0':
                    notcomplete.append(ticket.get('id'))

            if len(notcomplete):
                logger.info('waiting to complete uploading of <%d> pictures' % len(notcomplete))
                time.sleep(5)
            else:
                completed = True

    @staticmethod
    def get_signature_tag(signature):
        return '{machine_tag_signature}="{signature}"'.format(machine_tag_signature=Flickr.MACHINE_TAG_SIGNATURE, signature=signature)

    @staticmethod
    def __get_signature(machinetags):
        thesignature = None
        m = re.search('%s=([0-9a-f]+)( |$)' % Flickr.MACHINE_TAG_SIGNATURE, machinetags, re.IGNORECASE)

        if m:
            thesignature = m.group(1)

        return thesignature

    @staticmethod
    def __get_short_name(photo):
        temp = general.get_flickr_title(photo.get('title'), photo.get('originalsecret'))
        return general.get_short_name(temp)
