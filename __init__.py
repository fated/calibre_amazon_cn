#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import, print_function)

__license__   = 'GPL v3'
__copyright__ = '2011, Grant Drake <grant.drake@gmail.com>; 2013, Bruce Chou <brucechou24@gmail.com>'
__docformat__ = 'restructuredtext en'

import socket, time, re
from threading import Thread
from Queue import Queue, Empty

from calibre import as_unicode
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.sources.base import (Source, Option, fixcase, fixauthors)
from calibre.ebooks.metadata.book.base import Metadata
from calibre.utils.localization import canonicalize_lang

def CSSSelect(expr):
    from cssselect import HTMLTranslator
    from lxml.etree import XPath
    return XPath(HTMLTranslator().css_to_xpath(expr))

class Amazon_CN(Source):

    name = 'Amazon_CN'
    description = _('Downloads metadata and covers from Amazon.cn')
    author = 'Bruce Chou'
    version = (0, 1, 0)
    minimum_calibre_version = (0, 8, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset(['title', 'authors', 'identifier:amazon_cn',
        'identifier:isbn', 'rating', 'comments', 'publisher', 'pubdate',
        'languages', 'series'])
    has_html_comments = True
    supports_gzip_transfer_encoding = True

    BASE_URL = 'http://www.amazon.cn'
    MAX_EDITIONS = 5

    def __init__(self, *args, **kwargs):
        Source.__init__(self, *args, **kwargs)
        self.set_amazon_id_touched_fields()

    def test_fields(self, mi):
        '''
        Return the first field from self.touched_fields that is null on the mi object
        '''
        for key in self.touched_fields:
            if key.startswith('identifier:'):
                key = key.partition(':')[-1]
                if key == 'amazon':
                    if self.domain == 'cn':
                        key += '_' + self.domain
                if not mi.has_identifier(key):
                    return 'identifier:' + key
            elif mi.is_null(key):
                return key

    def save_settings(self, *args, **kwargs):
        Source.save_settings(self, *args, **kwargs)
        self.set_amazon_id_touched_fields()

    def set_amazon_id_touched_fields(self):
        ident_name = "identifier:amazon_cn"
        tf = [x for x in self.touched_fields if not
                x.startswith('identifier:amazon_cn')] + [ident_name]
        self.touched_fields = frozenset(tf)

    def get_asin(self, identifiers):
        for key, val in identifiers.iteritems():
            key = key.lower()
            if key in ('amazon_cn', 'asin'):
                return val
        return None

    def get_book_url(self, identifiers):  # {{{
        asin = self.get_asin(identifiers)
        if asin:
            url = 'http://www.amazon.cn/dp/'+asin
            idtype = 'amazon_cn'
            return (idtype, asin, url)

    def get_book_url_name(self, idtype, idval, url):
        if idtype == 'amazon_cn':
            return self.name
    # }}}

    def create_query(self, log, title=None, authors=None, identifiers={}): # {{{
        from urllib import urlencode

        asin = self.get_asin(identifiers)

        # See the amazon detailed search page to get all options
        q = {'search-alias': 'aps',
             'unfiltered': '1', }
        q['sort'] = 'relevance_rank'

        isbn = check_isbn(identifiers.get('isbn', None))

        if asin is not None:
            q['field-keywords'] = asin
        elif isbn is not None:
            q['field-isbn'] = isbn
        else:
            # Only return digital-book results
            q['search-alias'] = 'digital-text'
            if title:
                title_tokens = list(self.get_title_tokens(title))
                if title_tokens:
                    q['field-title'] = ' '.join(title_tokens)
            if authors:
                author_tokens = self.get_author_tokens(authors,
                        only_first_author=True)
                if author_tokens:
                    q['field-author'] = ' '.join(author_tokens)

        if not ('field-keywords' in q or 'field-isbn' in q or
                ('field-title' in q)):
            # Insufficient metadata to make an identify query
            return None

        # magic parameter to enable Chinese GBK encoding.
        q['__mk_zh_CN'] = u'亚马逊网站'

        encode_to = 'utf8'
        encoded_q = dict([(x.encode(encode_to, 'ignore'), y.encode(encode_to, 'ignore')) for x, y in q.iteritems()])
        url = 'http://www.amazon.cn/s/?' + urlencode(encoded_q)
        return url

    # }}}

    def get_cached_cover_url(self, identifiers):  # {{{
        url = None
        asin = self.get_asin(identifiers)
        if asin is None:
            isbn = identifiers.get('isbn', None)
            if isbn is not None:
                asin = self.cached_isbn_to_identifier(isbn)
        if asin is not None:
            url = self.cached_identifier_to_cover_url(asin)

        return url
    # }}}

    def parse_results_page(self, root):  # {{{
        from lxml.html import tostring

        matches = []

        def title_ok(title):
            title = title.lower()
            bad = []
            # bad.extend(['(%s edition)' % x for x in ('spanish', 'german')])
            for x in bad:
                if x in title:
                    return False
            return True

        for div in root.xpath(r'//div[starts-with(@id, "result_")]'):
            links = div.xpath(r'descendant::a[@class="title" and @href]')
            if not links:
                # New amazon markup
                links = div.xpath('descendant::h3/a[@href]')
            for a in links:
                title = tostring(a, method='text', encoding=unicode)
                if title_ok(title):
                    matches.append(a.get('href'))
                break

        if not matches:
            # This can happen for some user agents that Amazon thinks are
            # mobile/less capable
            for td in root.xpath(
                r'//div[@id="Results"]/descendant::td[starts-with(@id, "search:Td:")]'):
                for a in td.xpath(r'descendant::td[@class="dataColumn"]/descendant::a[@href]/span[@class="srTitle"]/..'):
                    title = tostring(a, method='text', encoding=unicode)
                    if title_ok(title):
                        matches.append(a.get('href'))
                    break

        # Keep only the top MAX_EDITIONS matches as the matches are sorted by relevance by Amazon so lower matches are not likely to be very relevant
        return matches[:MAX_EDITIONS]
    # }}}

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):  # {{{
        '''
        Note this method will retry without identifiers automatically if no match is found with identifiers.
        '''
        from calibre.utils.cleantext import clean_ascii_chars
        from calibre.ebooks.chardet import xml_to_unicode
        from lxml.html import tostring
        import html5lib

        testing = getattr(self, 'running_a_test', False)

        query = self.create_query(log, title=title, authors=authors,
                identifiers=identifiers)
        if query is None:
            log.error('Insufficient metadata to construct query')
            return
        br = self.browser
        if testing:
            print ('Using user agent for amazon: %s'%self.user_agent)
        try:
            raw = br.open_novisit(query, timeout=timeout).read().strip()
        except Exception as e:
            if callable(getattr(e, 'getcode', None)) and \
                    e.getcode() == 404:
                log.error('Query malformed: %r'%query)
                return
            attr = getattr(e, 'args', [None])
            attr = attr if attr else [None]
            if isinstance(attr[0], socket.timeout):
                msg = _('Amazon timed out. Try again later.')
                log.error(msg)
            else:
                msg = 'Failed to make identify query: %r'%query
                log.exception(msg)
            return as_unicode(msg)

        raw = clean_ascii_chars(xml_to_unicode(raw,
            strip_encoding_pats=True, resolve_entities=True)[0])

        if testing:
            import tempfile
            with tempfile.NamedTemporaryFile(prefix='amazon_results_',
                    suffix='.html', delete=False) as f:
                f.write(raw.encode('utf-8'))
            print ('Downloaded html for results page saved in', f.name)

        matches = []
        found = '<title>404 - ' not in raw

        if found:
            try:
                root = html5lib.parse(raw, treebuilder='lxml',
                        namespaceHTMLElements=False)
            except:
                msg = 'Failed to parse amazon page for query: %r'%query
                log.exception(msg)
                return msg

                errmsg = root.xpath('//*[@id="errorMessage"]')
                if errmsg:
                    msg = tostring(errmsg, method='text', encoding=unicode).strip()
                    log.error(msg)
                    # The error is almost always a not found error
                    found = False

        if found:
            matches = self.parse_results_page(root)

        if abort.is_set():
            return

        if not matches:
            if identifiers and title and authors:
                log('No matches found with identifiers, retrying using only'
                        ' title and authors. Query: %r'%query)
                return self.identify(log, result_queue, abort, title=title,
                        authors=authors, timeout=timeout)
            log.error('No matches found with query: %r'%query)
            return

        workers = [Worker(url, result_queue, br, log, i, domain, self,
                            testing=testing) for i, url in enumerate(matches)]

        for w in workers:
            w.start()
            # Don't send all requests at the same time
            time.sleep(0.1)

        while not abort.is_set():
            a_worker_is_alive = False
            for w in workers:
                w.join(0.2)
                if abort.is_set():
                    break
                if w.is_alive():
                    a_worker_is_alive = True
            if not a_worker_is_alive:
                break

        return None
    # }}}

    def download_cover(self, log, result_queue, abort,  # {{{
            title=None, authors=None, identifiers={}, timeout=30, get_best_cover=False):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(log, rq, abort, title=title, authors=authors, identifiers=identifiers)
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return

        if abort.is_set():
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)
    # }}}