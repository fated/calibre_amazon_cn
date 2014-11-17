Calibre Metadata Source Plugin for Amazon.cn
=========

This plugin allows Calibre to read book information from Amazon.cn when you choose to download/fetch metadata. Calibre currently comes with plugins for a number of information sources such as Amazon and Googlebooks. Adding this plugin can potentially increase both the success rate and quality of information retrieved for some of your Chinese books.

### Main Features of v0.3.0
This plugin can retrieve amazon_cn id, title, author, comments, rating, publisher, publication date, language, tags and covers from Amazon.cn. The amazon_cn id will also be displayed in the book details panel as "Amazon.cn" to be clicked on and taken directly to the website for that book.

### Special Notes:
* Requires Calibre 0.8 or later.
* No ISBN information for kindle books can be retrieved from Amazon.cn since almost all chinese kindle books have no ISBN information.

### Installation Notes:
Download the zip file and install the plugin as described in the Introduction to plugins thread.
Note that this is not a GUI plugin so it is not intended/cannot be added to context menus/toolbars etc.

### Paypal Donations:
If you find this plugin useful please feel free to show your appreciation. I have spent many unpaid hours in its development and support so any encouragement for me to continue is appreciated!

<a href="https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=9ZTDX8RL5P5E6">
<img src="https://www.paypalobjects.com/en_US/GB/i/btn/btn_donateCC_LG.gif" alt="PayPal – The safer, easier way to pay online."/>
</a>

### Version History:
* __Version 0.3.0__ - 17 Nov 2014  
    Fix parsing issue caused by new Amazon style.
    Fix extra CSS style info in title when parsing.
* __Version 0.2.0__ - 04 Nov 2013  
    Add support for parsing tags.
* __Version 0.1.0__ - 30 Oct 2013  
    Initial release of plugin.
