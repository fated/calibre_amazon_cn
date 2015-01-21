#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__copyright__ = '2011, Grant Drake <grant.drake@gmail.com>; 2013, Bruce Chou <brucechou24@gmail.com>'
__docformat__ = 'restructuredtext en'

import socket, re, datetime
from collections import OrderedDict
from threading import Thread

from lxml.html import fromstring, tostring

from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.comments import sanitize_comments_html
from calibre.utils.cleantext import clean_ascii_chars
from calibre.utils.localization import canonicalize_lang

def CSSSelect(expr):
    from cssselect import HTMLTranslator
    from lxml.etree import XPath
    return XPath(HTMLTranslator().css_to_xpath(expr))

class Worker(Thread):  # Get details {{{

    '''
    Get book details from amazons book page in a separate thread
    '''

    def __init__(self, url, result_queue, browser, log, relevance, plugin,
            timeout=20, testing=False):
        Thread.__init__(self)
        self.daemon = True
        self.testing = testing
        self.url, self.result_queue = url, result_queue
        self.log, self.timeout = log, timeout
        self.relevance, self.plugin = relevance, plugin
        self.browser = browser.clone_browser()
        self.cover_url = self.amazon_id = self.isbn = None
        from lxml.html import tostring
        self.tostring = tostring

        self.months = {
            1: [u'1月'],
            2: [u'2月'],
            3: [u'3月'],
            4: [u'4月'],
            5: [u'5月'],
            6: [u'6月'],
            7: [u'7月'],
            8: [u'8月'],
            9: [u'9月'],
            10: [u'10月'],
            11: [u'11月'],
            12: [u'12月'],
        }

        self.english_months = [None, 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

        self.pd_xpath = '''
            //h2[starts-with(text(), "基本信息")]/../div[@class="content"]
            '''
        self.publisher_xpath = '''
            descendant::*[starts-with(text(), "出版社:")]
            '''
        self.publisher_names = {'出版社'}

        self.language_xpath =    '''
            descendant::*[starts-with(text(), "语种：")]
            '''
        self.language_names = {'语种'}

        self.tags_xpath = '''
            descendant::h2[text() = "\t 查找其它相似商品"]/../descendant::ul/li
        '''

        self.ratings_pat = re.compile(r'(平均)([0-9.]+)( (星))')

        lm = {
                'eng': ('English', 'Englisch'),
                'fra': ('French', 'Français'),
                'ita': ('Italian', 'Italiano'),
                'deu': ('German', 'Deutsch'),
                'spa': ('Spanish', 'Espa\xf1ol', 'Espaniol'),
                'jpn': ('Japanese', u'日本語'),
                'por': ('Portuguese', 'Português'),
                'chs': ('Chinese (Simplified)', u'简体中文'),
                'cht': ('Chinese (Traditional)', u'繁体中文'),
                }
        self.lang_map = {}
        for code, names in lm.iteritems():
            for name in names:
                self.lang_map[name] = code

        self.series_pat = re.compile(
                r'''
                \|\s*              # Prefix
                (Series)\s*:\s*    # Series declaration
                (?P<series>.+?)\s+  # The series name
                \((Book)\s*    # Book declaration
                (?P<index>[0-9.]+) # Series index
                \s*\)
                ''', re.X)

    def delocalize_datestr(self, raw):
        if raw:
            ans = raw
            ans = ans.replace(u'年', ' ')
            ans = ans.replace(u'日', '')
            for i, vals in self.months.iteritems():
                for x in vals:
                    ans = ans.replace(x, self.english_months[i]+' ')
            return ans

    def run(self):
        try:
            self.get_details()
        except:
            self.log.exception('get_details failed for url: %r'%self.url)

    def get_details(self):
        from calibre.utils.cleantext import clean_ascii_chars
        from calibre.ebooks.chardet import xml_to_unicode
        import html5lib

        try:
            raw = self.browser.open_novisit(self.url, timeout=self.timeout).read().strip()
        except Exception as e:
            if callable(getattr(e, 'getcode', None)) and \
                    e.getcode() == 404:
                self.log.error('URL malformed: %r'%self.url)
                return
            attr = getattr(e, 'args', [None])
            attr = attr if attr else [None]
            if isinstance(attr[0], socket.timeout):
                msg = 'Amazon timed out. Try again later.'
                self.log.error(msg)
            else:
                msg = 'Failed to make details query: %r'%self.url
                self.log.exception(msg)
            return

        oraw = raw
        raw = xml_to_unicode(raw, strip_encoding_pats=True,
                resolve_entities=True)[0]
        if '<title>404 - ' in raw:
            self.log.error('URL malformed: %r'%self.url)
            return

        try:
            root = html5lib.parse(clean_ascii_chars(raw), treebuilder='lxml',
                    namespaceHTMLElements=False)
        except:
            msg = 'Failed to parse amazon details page: %r'%self.url
            self.log.exception(msg)
            return

        errmsg = root.xpath('//*[@id="errorMessage"]')
        if errmsg:
            msg = 'Failed to parse amazon details page: %r'%self.url
            msg += self.tostring(errmsg, method='text', encoding=unicode).strip()
            self.log.error(msg)
            return

        self.parse_details(oraw, root)

    def parse_details(self, raw, root):
        try:
            asin = self.parse_asin(root)
        except:
            self.log.exception('Error parsing asin for url: %r'%self.url)
            asin = None
        if self.testing:
            import tempfile, uuid
            with tempfile.NamedTemporaryFile(prefix=(asin or str(uuid.uuid4()))+ '_',
                    suffix='.html', delete=False) as f:
                f.write(raw)
            print ('Downloaded html for', asin, 'saved in', f.name)

        try:
            title = self.parse_title(root)
        except:
            self.log.exception('Error parsing title for url: %r'%self.url)
            title = None

        try:
            authors = self.parse_authors(root)
        except:
            self.log.exception('Error parsing authors for url: %r'%self.url)
            authors = []

        if not title or not authors or not asin:
            self.log.error('Could not find title/authors/asin for %r'%self.url)
            self.log.error('ASIN: %r Title: %r Authors: %r'%(asin, title,
                authors))
            return

        mi = Metadata(title, authors)
        idtype = 'amazon_cn'
        mi.set_identifier(idtype, asin)
        self.amazon_id = asin

        try:
            mi.rating = self.parse_rating(root)
        except:
            self.log.exception('Error parsing ratings for url: %r'%self.url)

        try:
            mi.comments = self.parse_comments(root)
        except:
            self.log.exception('Error parsing comments for url: %r'%self.url)

        try:
            series, series_index = self.parse_series(root)
            if series:
                mi.series, mi.series_index = series, series_index
            elif self.testing:
                mi.series, mi.series_index = 'Dummy series for testing', 1
        except:
            self.log.exception('Error parsing series for url: %r'%self.url)

        try:
            mi.tags = self.parse_tags(root)
        except:
            self.log.exception('Error parsing tags for url: %r'%self.url)

        try:
            self.cover_url = self.parse_cover(root, raw)
        except:
            self.log.exception('Error parsing cover for url: %r'%self.url)
        mi.has_cover = bool(self.cover_url)

        non_hero = CSSSelect('div#bookDetails_container_div div#nonHeroSection')(root)
        if non_hero:
            # New style markup
            try:
                self.parse_new_details(root, mi, non_hero[0])
            except:
                self.log.exception('Failed to parse new-style book details section')
        else:
            pd = root.xpath(self.pd_xpath)
            if pd:
                pd = pd[0]

                try:
                    isbn = self.parse_isbn(pd)
                    if isbn:
                        self.isbn = mi.isbn = isbn
                except:
                    self.log.exception('Error parsing ISBN for url: %r'%self.url)

                try:
                    mi.publisher = self.parse_publisher(pd)
                except:
                    self.log.exception('Error parsing publisher for url: %r'%self.url)

                try:
                    mi.pubdate = self.parse_pubdate(pd)
                except:
                    self.log.exception('Error parsing publish date for url: %r'%self.url)

                try:
                    lang = self.parse_language(pd)
                    if lang:
                        mi.language = lang
                except:
                    self.log.exception('Error parsing language for url: %r'%self.url)

            else:
                self.log.warning('Failed to find product description for url: %r'%self.url)

        mi.source_relevance = self.relevance

        if self.amazon_id:
            if self.isbn:
                self.plugin.cache_isbn_to_identifier(self.isbn, self.amazon_id)
            if self.cover_url:
                self.plugin.cache_identifier_to_cover_url(self.amazon_id,
                        self.cover_url)

        self.plugin.clean_downloaded_metadata(mi)

        self.result_queue.put(mi)

    def parse_asin(self, root):
        link = root.xpath('//link[@rel="canonical" and @href]')
        for l in link:
            return l.get('href').rpartition('/')[-1]

    def totext(self, elem):
        return self.tostring(elem, encoding=unicode, method='text').strip()

    def parse_title(self, root):
        h1 = root.xpath('//h1[@id="title"]')
        if h1:
            h1 = h1[0]
            for child in h1.xpath('./*[contains(@class, "a-color-secondary")]'):
                h1.remove(child)
            return self.totext(h1)
        tdiv = root.xpath('//h1[contains(@class, "parseasinTitle")]')[0]
        ttdiv = tdiv.xpath('descendant::*[@id="btAsinTitle"]')[0]
        actual_title = ttdiv.xpath('descendant::*[@style="padding-left: 0"]')
        if actual_title:
            title = self.tostring(actual_title[0], encoding=unicode,
                    method='text').strip()
        else:
            title = self.tostring(tdiv, encoding=unicode, method='text').strip()
        ans = re.sub(r'[(\[].*[)\]]', '', title).strip()
        if not ans:
            ans = title.rpartition('[')[0].strip()
        return ans

    def parse_authors(self, root):
        matches = CSSSelect('#byline .author .contributorNameID')(root)
        if not matches:
            matches = CSSSelect('#byline .author a.a-link-normal')(root)
        if matches:
            authors = [self.totext(x) for x in matches]
            return [a for a in authors if a]

        x = '//h1[contains(@class, "parseasinTitle")]/following-sibling::span/*[(name()="a" and @href) or (name()="span" and @class="contributorNameTrigger")]'
        aname = root.xpath(x)
        if not aname:
            aname = root.xpath('''
            //h1[contains(@class, "parseasinTitle")]/following-sibling::*[(name()="a" and @href) or (name()="span" and @class="contributorNameTrigger")]
                    ''')
        for x in aname:
            x.tail = ''
        authors = [self.tostring(x, encoding=unicode, method='text').strip() for x
                in aname]
        authors = [a for a in authors if a]
        return authors

    def parse_rating(self, root):
        rating_paths = ('//div[@data-feature-name="averageCustomerReviews"]',
                        '//div[@class="jumpBar"]/descendant::span[contains(@class,"asinReviewsSummary")]',
                        '//div[@class="buying"]/descendant::span[contains(@class,"asinReviewsSummary")]',
                        '//span[@class="crAvgStars"]/descendant::span[contains(@class,"asinReviewsSummary")]')
        ratings = None
        for p in rating_paths:
            ratings = root.xpath(p)
            if ratings:
                break
        if ratings:
            for elem in ratings[0].xpath('descendant::*[@title]'):
                t = elem.get('title').strip()
                m = self.ratings_pat.match(t)
                if m is not None:
                    return float(m.group(2))

    def _render_comments(self, desc):
        from calibre.library.comments import sanitize_comments_html

        for c in desc.xpath('descendant::noscript'):
            c.getparent().remove(c)
        for c in desc.xpath('descendant::*[@class="seeAll" or'
                ' @class="emptyClear" or @id="collapsePS" or'
                ' @id="expandPS"]'):
            c.getparent().remove(c)

        for a in desc.xpath('descendant::a[@href]'):
            del a.attrib['href']
            a.tag = 'span'
        desc = self.tostring(desc, method='html', encoding=unicode).strip()

        # Encoding bug in Amazon data U+fffd (replacement char)
        # in some examples it is present in place of '
        desc = desc.replace('\ufffd', "'")
        # remove all attributes from tags
        desc = re.sub(r'<([a-zA-Z0-9]+)\s[^>]+>', r'<\1>', desc)
        # Collapse whitespace
        # desc = re.sub('\n+', '\n', desc)
        # desc = re.sub(' +', ' ', desc)
        # Remove the notice about text referring to out of print editions
        desc = re.sub(r'(?s)<em>--This text ref.*?</em>', '', desc)
        # Remove comments
        desc = re.sub(r'(?s)<!--.*?-->', '', desc)
        return sanitize_comments_html(desc)

    def parse_comments(self, root):
        ns = CSSSelect('#bookDescription_feature_div noscript')(root)
        if ns:
            ns = ns[0]
            if len(ns) == 0 and ns.text:
                import html5lib
                # html5lib parsed noscript as CDATA
                ns = html5lib.parseFragment('<div>%s</div>' % (ns.text), treebuilder='lxml', namespaceHTMLElements=False)[0]
            else:
                ns.tag = 'div'
            return self._render_comments(ns)

        ans = ''
        desc = root.xpath('//div[@id="ps-content"]/div[@class="content"]')
        if desc:
            ans = self._render_comments(desc[0])

        desc = root.xpath('//div[@id="productDescription"]/*[@class="content"]')
        if desc:
            ans += self._render_comments(desc[0])
        return ans

    def parse_series(self, root):
        ans = (None, None)
        desc = root.xpath('//div[@id="ps-content"]/div[@class="buying"]')
        if desc:
            raw = self.tostring(desc[0], method='text', encoding=unicode)
            raw = re.sub(r'\s+', ' ', raw)
            match = self.series_pat.search(raw)
            if match is not None:
                s, i = match.group('series'), float(match.group('index'))
                if s:
                    ans = (s, i)
        return ans

    def parse_tags(self, root):
        ans = []
        exclude_tokens = {'kindle', 'a-z'}
        exclude = {u'kindle电子书', 'by authors', 'authors & illustrators', 'books', 'new; used & rental textbooks'}
        seen = set()
        for li in root.xpath(self.tags_xpath):
            for i, a in enumerate(li.iterdescendants('a')):
                if i > 0:
                    # we ignore the first category since it is almost always too broad
                    raw = (a.text or '').strip().replace(',', ';')
                    lraw = icu_lower(raw)
                    tokens = frozenset(lraw.split())
                    if raw and lraw not in exclude and not tokens.intersection(exclude_tokens) and lraw not in seen:
                        ans.append(raw)
                        seen.add(lraw)
        return ans

    def parse_cover(self, root, raw=b""):
        import urllib
        imgs_url = 'http://z2-ec2.images-amazon.com/images/P/'+self.amazon_id+'.01.MAIN._SCRM_.jpg'

        try:
            res = urllib.urlopen(imgs_url)
            code = res.getcode()
            res.close()
        except Exception,e:
            code = 404

        if code == 200:
            return imgs_url

        imgs = root.xpath('//img[(@id="prodImage" or @id="original-main-image" or @id="main-image") and @src]')
        if not imgs:
            imgs = root.xpath('//div[@class="main-image-inner-wrapper"]/img[@src]')
            if not imgs:
                imgs = root.xpath('//div[@id="main-image-container"]//img[@src]')
        if imgs:
            src = imgs[0].get('src')
            if 'loading-' in src:
                js_img = re.search(br'"largeImage":"(http://[^"]+)",',raw)
                if js_img:
                    src = js_img.group(1).decode('utf-8')
            if ('/no-image-avail' not in src and 'loading-' not in src and '/no-img-sm' not in src):
                self.log('Found image: %s' % src)
                parts = src.split('/')
                if len(parts) > 3:
                    bn = parts[-1]
                    sparts = bn.split('_')
                    if len(sparts) > 2:
                        bn = re.sub(r'\.\.jpg$', '.jpg', (sparts[0] + sparts[-1]))
                        return ('/'.join(parts[:-1]))+'/'+bn

    def parse_new_details(self, root, mi, non_hero):
        table = non_hero.xpath('descendant::table')[0]
        for tr in table.xpath('descendant::tr'):
            cells = tr.xpath('descendant::td')
            if len(cells) == 2:
                name = self.totext(cells[0])
                val = self.totext(cells[1])
                if not val:
                    continue
                if name in self.language_names:
                    ans = self.lang_map.get(val, None)
                    if not ans:
                        ans = canonicalize_lang(val)
                    if ans:
                        mi.language = ans
                elif name in self.publisher_names:
                    pub = val.partition(';')[0].partition('(')[0].strip()
                    if pub:
                        mi.publisher = pub
                    date = val.rpartition('(')[-1].replace(')', '').strip()
                    try:
                        from calibre.utils.date import parse_only_date
                        date = self.delocalize_datestr(date)
                        mi.pubdate = parse_only_date(date, assume_utc=True)
                    except:
                        self.log.exception('Failed to parse pubdate: %s' % val)
                elif name in {'ISBN', 'ISBN-10', 'ISBN-13'}:
                    ans = check_isbn(val)
                    if ans:
                        self.isbn = mi.isbn = ans

    def parse_isbn(self, pd):
        items = pd.xpath(
            'descendant::*[starts-with(text(), "ISBN")]')
        if not items:
            items = pd.xpath(
                'descendant::b[contains(text(), "ISBN:")]')
        for x in reversed(items):
            if x.tail:
                ans = check_isbn(x.tail.strip())
                if ans:
                    return ans

    def parse_publisher(self, pd):
        for x in reversed(pd.xpath(self.publisher_xpath)):
            if x.tail:
                ans = x.tail.partition(';')[0]
                return ans.partition('(')[0].strip()

    def parse_pubdate(self, pd):
        for x in reversed(pd.xpath(self.publisher_xpath)):
            if x.tail:
                from calibre.utils.date import parse_only_date
                ans = x.tail
                date = ans.rpartition('(')[-1].replace(')', '').strip()
                date = self.delocalize_datestr(date)
                return parse_only_date(date, assume_utc=True)

    def parse_language(self, pd):
        for x in reversed(pd.xpath(self.language_xpath)):
            if x.tail:
                raw = x.tail.strip().partition(',')[0].strip()
                ans = self.lang_map.get(raw, None)
                if ans:
                    return ans
                ans = canonicalize_lang(ans)
                if ans:
                    return ans
# }}}