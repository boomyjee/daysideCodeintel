import os,json
from autocomplete import unmark_text
from autocomplete import Autocomplete 

Autocomplete.params = '{"authInclude":"/var/www/uxcandy_boomyjee/data/public_html/dayside/server/api.php","authFunction":["\\\\FileApi","remote_auth"]}'

class Request:
    def __init__(self):
        self.headers = {"Cookie":'uxcandy_auth=boomyjee-676789c2bbd4edee8a65bb95970b34c9820d2662; editor_auth=40bd001563085fc35165329ea1ff5c5ecbdbbeef'}

a = Autocomplete(0,0,0)
a.request = Request()

print a.auth()