"""
Email Sending Module

This module provides functionality to send emails with support for various formats
and attachments. It handles plain text and HTML content, inline elements (such as images),
and different types of attachments. The module automatically structures the email
using appropriate MIME types, ensuring compatibility with email clients.

Key Features:
-------------
- Supports sending emails with plain text and HTML content.
- Allows for specifying multiple recipients using 'to', 'cc', and 'bcc' fields.
- Inline elements can be embedded in HTML emails, with support for referencing by Content-ID (CID).
- Attachments of various types, including files, raw data, and URLs, can be added to emails.
- Handles complex MIME structures like `multipart/alternative`, `multipart/related`, and `multipart/mixed`
  to ensure proper formatting of emails.
- Additional headers can be specified to customize email metadata.

Usage:
------
The `Mailer.send` function is the primary interface for sending emails. It accepts various parameters
for customization, allowing users to define the sender, recipients, content, and attachments in a flexible manner.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.nonmultipart import MIMENonMultipart
from email.charset import Charset
from email.utils import parseaddr
from email import encoders
from enum import Enum
import os
import socket
import mimetypes
import urllib.parse
import urllib.error
import urllib.request
import posixpath

import logging
logger = logging.getLogger(__name__)

class MailerException(Exception):
    pass

charsetUtf8 = Charset('utf-8')

class Cipher(Enum):
    PLAIN = 0
    SSL = 1
    START_TLS = 2

    @staticmethod
    def from_port(port):
        # Source: https://www.mailgun.com/blog/which-smtp-port-understanding-ports-25-465-587/
        return {
            587: Cipher.START_TLS,
            2525: Cipher.START_TLS,
            465: Cipher.SSL
        }.get(port, Cipher.PLAIN)

class MIMEAnyType(MIMENonMultipart):
    def __init__(self, _data, _maintype = 'application', _subtype = 'octet-stream', **_params):
        if _maintype is None:
            raise TypeError('Invalid MIME main type.')
        if _subtype is None:
            raise TypeError('Invalid MIME sub type.')

        MIMENonMultipart.__init__(self, _maintype, _subtype, policy = None, **_params)
        self.set_payload(_data)
        encoders.encode_base64(self)

class MailAddress():
    """ Try somehow to convert the object to a valid mail address field.

    Valid fields can be:
        "Jon Doe <jon.doe@example.com>"
        "jon.doe@example.com"
    """

    def __init__(self, obj, mailer = None):
        self._mailer = mailer
        self._obj = obj
        self._name = None
        self._address = None
        self._extract()

    def _extract(self):
        o = self._obj

        if isinstance(o, str):
            self._name, self._address = parseaddr(o)

        elif isinstance(o, dict):
            self._name = o.get('fullname', None) or o.get('name', None) or o.get('id', None)
            self._address = parseaddr(o.get('mail', None) or o.get('email', None) or o.get('e-mail', None) or o.get('mailaddress', None))[1]

    def is_valid(self):
        return bool(self._address)

    def __str__(self):
        if not self._address:
            return ""

        if self._name:
            encoded_name = charsetUtf8.header_encode(self._name)
            return f"{encoded_name} <{self._address}>"

        return self._address

    def __repr__(self):
        return f"<{self.__class__} f{self.__str__()}>"

class Mailer(object):

    Cipher = Cipher

    @staticmethod
    def from_env_vars():
        """
        Reads all necessary data from environment variables and creates a Mailer instance.
        If login credentials are also provided, it automatically logs in, allowing you
        to directly use the `send()` method.

        Environment Variables:
        ----------------------
        SMTP_HOST : str
            Specifies the hostname or IP address of the mail server.
        SMTP_PORT : int
            Must be a number that specifies the port of the mail server.
        SMTP_CIPHER : str
            Must be one of the values from the Cipher enumeration (see above).
        SMTP_USER : str
            The SMTP user to be used, usually an email address.
        SMTP_PASSWORD : str
            The password for the SMTP user.

        Defaults:
        ---------
        SMTP_HOST : str
            The hostname of the machine where the code is currently running.
        SMTP_PORT : int
            25
        SMTP_CIPHER : str
            PLAIN

        Example:
        --------
        SMTP_HOST : mail.example.com
        SMTP_PORT : 587
        SMTP_CIPHER : START_TLS
        SMTP_USER : postmaster@example.com
        SMTP_PASSWORD : ***

        Automatic login only works if both SMTP_USER and SMTP_PASSWORD are provided.
        Otherwise, it will be ignored.
        """

        host = os.environ.get('SMTP_HOST', socket.gethostname())

        if not host:
            raise MailerException('Environment variable missing: SMTP_HOST')

        port = os.environ.get('SMTP_PORT', None)
        if port:
            try:
                port = int(port)
            except ValueError as exc:
                raise MailerException(f"SMTP Port has to be an integer but is '{port}'.") from exc
        else:
            port = 25

        cipher = os.environ.get('SMTP_CIPHER', None)
        if cipher:
            try:
                cipher = Cipher[cipher]
            except KeyError as exc:
                raise MailerException(f"Unknown Cipher '{cipher}', must be one of {', '.join(map(lambda x: x.name, Cipher))}.") from exc
        else:
            cipher = Cipher.from_port(port)

        mailer = Mailer(host = os.environ['SMTP_HOST'], port = port, cipher = cipher)
        try:
            mailer.connect()
        except smtplib.SMTPConnectError as exc:
            raise MailerException("Error occurred during establishment of a connection with the server.") from exc

        if ('SMTP_USER' in os.environ) and ('SMTP_PASSWORD' in os.environ):
            try:
                mailer.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
            except smtplib.SMTPHeloError as exc:
                raise MailerException("The server didn't reply properly to the HELO greeting.") from exc
            except smtplib.SMTPAuthenticationError as exc:
                raise MailerException("The server didn't accept the username/password combination.") from exc
            except smtplib.SMTPNotSupportedError as exc:
                raise MailerException("The AUTH command is not supported by the server.") from exc
            except smtplib.SMTPException as exc:
                raise MailerException("No suitable authentication method was found.") from exc

        return mailer

    def __init__(self, host, port = 0, cipher = None):
        self._host = host
        self._port = port or 0
        self._conn = None
        self._cipher = cipher or Cipher.from_port(self._port)
        self._user = None

    def connect(self):
        if self._cipher == Cipher.SSL:
            self._conn = smtplib.SMTP_SSL(host = self._host, port = self._port)
        else:
            self._conn = smtplib.SMTP(host = self._host, port = self._port)
            if self._cipher == Cipher.START_TLS:
                self._conn.starttls()

    def connected(self):
        return isinstance(self._conn, smtplib.SMTP)

    def login(self, user, password):
        if self._conn is None:
            self.connect()
        self._user = user
        self._conn.login(user, password)

    def quit(self):
        self._conn.quit()
        self._conn = None
        self._user = None

    def _convert_names(self, names, argname):
        if isinstance(names, str):
            logger.warning("'%s' of type 'str' is deprecated. Please use 'list', 'tuple' or 'set' instead.", argname)
            names = [names]
        if names is None:
            return None
        if not isinstance(names, (list, tuple, set)):
            raise MailerException(f"Argument '{argname}' has to be a of type 'list', 'tuple' or 'set'.")

        result = []
        for n in names:
            ma = MailAddress(n, self)
            if not ma.is_valid():
                logger.warning("Recipient in field '%s' is invalid and will be skipped: %s", argname, n)
                continue
            result.append(str(ma))

        return result

    def send(
        self,
        to = None,
        from_addr = None,
        subject = None,
        cc = None,
        bcc = None,
        reply_to = None,
        plaintext = None,
        html = None,
        attachments = None,
        inlines = None,
        header = None
        ):
        """
        Sends an email.

        Parameters:
        ----------
        to, cc, bcc : None, str, or list of str
            Recipients of the email. These can be None, a string, or a list of strings.
        from_addr : str or None
            The sender's email address. This should be a valid address; otherwise, most servers will reject it.
            If None, the user from the login (see `self.login()`) is used.
        subject : str or None
            The subject of the email. Can be None or a string.
        reply_to : str or None
            The reply-to address. Can be None or a string.
        plaintext : str
            Plain text content of the email, used if the recipient cannot read HTML emails.
        html : str
            HTML content of the email.
        attachments : list
            A list of attachments. Attachments can be of type 'str', 'bytes', or 'dict'.

            - str: The string is interpreted as a local file path, and the file name is used as the attachment name.
            - bytes: The object is treated as the content of a file, and the attachment name is auto-numbered.
            - dict: The dictionary can have the following optional keys:

                - name: Specifies the name of the attachment (optional).
                - type: Specifies the content type of the attachment (optional). If not provided, the content type is inferred from the name or file extension. Defaults to "application/octet-stream".

                The dict can contain only one of the following keys at a time:

                - data: The content of the attachment as 'bytes' or 'str'. If 'str', it is assumed to be 'utf-8' and is converted to 'bytes'.
                - file: A file path pointing to an existing file. The content of the file is attached. If 'name' is not provided, the file name is used as the attachment name.
                - url: A URL accessible by the script. The content from the URL is attached, and the content type and name are used if provided.

        inlines : list
            A list of inline elements, such as images, for the HTML view. Each element must be a dictionary with the following keys:

            - cid: The content ID used to reference the element.
            - type: Specifies the content type (optional).

            Data for the element can be provided in one of the following ways, similar to attachments:

            - data: The content of the file as 'bytes'.
            - file: A file path pointing to an existing file.
            - url: A URL accessible by the script.

        header : dict
            Additional mail header fields as a dictionary.
        """

        html_part = None
        plaintext_part = None
        attachment_parts = []
        inline_parts = []
        content = None

        recipients = []

        ## Check parameters

        # Convert to, cc und bcc to a list
        to = self._convert_names(to, 'to')
        cc = self._convert_names(cc, 'cc')
        bcc = self._convert_names(bcc, 'bcc')

        # If `from_addr` is not a string, we convert it to one, and if it's `None`, we use the login user.
        if from_addr is None:
            if not '@' in self._user:
                from_addr = self._user + '@' + self._host
            else:
                from_addr = self._user
        else:
            from_mailaddress = MailAddress(from_addr, self)
            if not from_mailaddress.is_valid():
                raise MailerException(f"Can not derive mail address from from_addr={from_addr}.")
            from_addr = str(from_mailaddress)

        if reply_to is not None:
            reply_to_mailaddress = MailAddress(reply_to, self)
            if not reply_to_mailaddress.is_valid():
                logger.warning("Can not derive mail address from from_addr=%s.", from_addr)
                reply_to = None
            else:
                reply_to = str(reply_to_mailaddress)

        if subject is None:
            subject = ''
        elif not isinstance(subject, str):
            subject = str(subject)

        # Convert `plaintext` to MIMEText
        if plaintext is not None:
            if not isinstance(plaintext, str):
                plaintext = str(plaintext)
            plaintext_part = MIMEText(plaintext, 'text')

        # Convert `html` to MIMEText
        if html is not None:
            if not isinstance(html, str):
                html = str(html)
            html_part = MIMEText(html, 'html')

        if attachments:
            if not isinstance(attachments, (list, tuple, set)):
                attachments = [attachments]

            for i, attachment in enumerate(attachments):
                if isinstance(attachment, str):
                    attachment = {'file': attachment}
                elif isinstance(attachment, bytes):
                    attachment = {'data': attachment}

                if not isinstance(attachment, dict):
                    raise MailerException(f"Attachment {i} has to by of type 'str', 'bytes' or 'dict' but is '{type(attachment)}'.")

                if 'data' in attachment:
                    attachment_data = attachment['data']
                    attachment_name = attachment.get('name', f"Unnamed attachment {i}.dat")

                    attachment_type = None
                    if 'type' in attachment:
                        attachment_type = attachment['type']
                    elif 'name' in attachment:
                        attachment_type = mimetypes.guess_type(attachment_name)[0]

                    if attachment_type is not None and '/' in attachment_type:
                        attachment_maintype, attachment_subtype = attachment_type.split('/')
                    else:
                        attachment_maintype, attachment_subtype = 'application', 'octet-stream'

                    if isinstance(attachment_data, str):
                        attachment_data = attachment_data.encode('utf-8')

                    if isinstance(attachment_data, bytes):
                        part = MIMEAnyType(attachment_data,
                                           attachment_maintype,
                                           attachment_subtype,
                                           Name = attachment_name)
                        part.add_header('Content-Disposition', f'attachment; filename="{attachment_name}"')

                    else:
                        raise MailerException(f"'data' entry in attachment {i} has to be of type 'str' or 'bytes' but is '{type(attachment_data)}'.")

                elif 'file' in attachment:
                    attachment_file = attachment['file']
                    if not os.path.isfile(attachment_file):
                        raise MailerException(f"File in attachment {i} does not exist: {attachment_file}")

                    attachment_type = attachment.get('type', mimetypes.guess_type(attachment_file)[0])
                    if attachment_type is not None and '/' in attachment_type:
                        attachment_maintype, attachment_subtype = attachment_type.split('/')
                    else:
                        attachment_maintype, attachment_subtype = 'application', 'octet-stream'

                    attachment_name = attachment.get('name', os.path.basename(attachment_file))

                    with open(attachment_file, "rb") as a:
                        part = MIMEAnyType(a.read(),
                                           attachment_maintype,
                                           attachment_subtype,
                                           Name = attachment_name)

                    part.add_header('Content-Disposition', f'attachment; filename="{attachment_name}"')

                elif 'url' in attachment:

                    req = urllib.request.Request(attachment['url'])

                    try:
                        with urllib.request.urlopen(req) as response:

                            attachment_type = attachment.get('type', response.info().get_content_type())
                            if (attachment_type is not None) and ('/' in attachment_type):
                                attachment_maintype, attachment_subtype = attachment_type.split('/')
                            else:
                                attachment_maintype, attachment_subtype = 'application', 'octet-stream'

                            attachment_name = attachment.get('name', response.info().get_filename())
                            if attachment_name is None:
                                attachment_name = posixpath.basename(urllib.parse.urlparse(req.get_full_url()).path)

                            if not attachment_name:
                                attachment_name = f"Unnamed attachment {i}" + (mimetypes.guess_extension(attachment_maintype + "/" + attachment_subtype) or '.dat')

                            part = MIMEAnyType(
                                response.read(),
                                attachment_maintype,
                                attachment_subtype,
                                Name = attachment_name
                            )

                            part.add_header('Content-Disposition', f'attachment; filename="{attachment_name}"')

                    except urllib.error.HTTPError as exc:
                        raise MailerException(f"Could not download URL {attachment['url']}: {exc.reason}") from exc

                else:
                    raise MailerException("Attachment {i} has to define at least one of 'data', 'url' or 'file'.")

                attachment_parts.append(part)

        if inlines:
            if not isinstance(inlines, (list, tuple, set)):
                inlines = [inlines]

            for i, inline in enumerate(inlines):
                if not 'cid' in inline:
                    raise MailerException(f"inlines[{i}]: For inline elements you need to specify 'cid'.")

                i_cid = inline['cid']
                if not '@' in i_cid:
                    # See also: https://stackoverflow.com/questions/39577386/the-precise-format-of-content-id-header
                    raise MailerException("inlines[%d]: The 'cid' value must conform to RFC 2822's msg-id grammar: id-left \"@\" id-right")

                i_name = i_cid.split('@')[0]

                if 'data' in inline:
                    i_data = inline['data']

                    if not isinstance(i_data, bytes):
                        raise MailerException(f"inlines[{i}]: 'data' has to be of type 'bytes'.")

                    i_type = inline.get('type', mimetypes.guess_type(i_name)[0])
                    if i_type is not None and '/' in i_type:
                        i_maintype, i_subtype = i_type.split('/')
                    else:
                        i_maintype, i_subtype = 'application', 'octet-stream'

                    part = MIMEAnyType(
                        i_data,
                        i_maintype,
                        i_subtype,
                        Name = i_name
                    )

                elif 'file' in inline:
                    i_file = inline['file']
                    if not os.path.isfile(i_file):
                        raise MailerException(f"inlines[{i}]: File does not exist: {i_file}")

                    i_type = inline.get('type', mimetypes.guess_type(i_file)[0]) or mimetypes.guess_type(i_name)[0]
                    if (i_type is not None) and ('/' in i_type):
                        i_maintype, i_subtype = i_type.split('/', 1)
                    else:
                        i_maintype, i_subtype = 'application', 'octet-stream'

                    with open(i_file, "rb") as a:
                        part = MIMEAnyType(
                            a.read(),
                            i_maintype,
                            i_subtype,
                            Name = i_name
                        )

                elif 'url' in inline:
                    req = urllib.request.Request(inline['url'])

                    try:
                        with urllib.request.urlopen(req) as response:

                            i_type = inline.get('type', response.info().get_content_type())
                            if i_type is not None and '/' in i_type:
                                i_maintype, i_subtype = i_type.split('/')
                            else:
                                i_maintype, i_subtype = 'application', 'octet-stream'

                            req_name = response.info().get_filename()
                            if req_name is None:
                                req_name = posixpath.basename(urllib.parse.urlparse(req.get_full_url()).path)

                            if req_name:
                                i_name = req_name

                            part = MIMEAnyType(
                                response.read(),
                                i_maintype,
                                i_subtype,
                                Name = i_name
                            )

                    except urllib.error.HTTPError as exc:
                        raise MailerException(f"Could not download URL {inline['url']}: {exc.reason}") from exc

                else:
                    raise MailerException("inlines[%d]: At least one of 'data', 'url' or 'file' should be defined.")

                part.add_header('Content-ID', '<' + i_cid + '>')
                part.add_header('Content-Disposition', 'inline')

                inline_parts.append(part)

        # If both text and HTML are present, wrap them in multipart/alternative.
        if (html_part is not None) and (plaintext_part is not None):
            body = MIMEMultipart('alternative')
            body.attach(plaintext_part)
            body.attach(html_part)
        elif html_part is not None:
            body = html_part
        elif plaintext_part is not None:
            body = plaintext_part
        else:
            body = None

        # If there are inline elements, package them together with the body in multipart/related.
        if inline_parts:
            if body is None:
                raise MailerException("Inline elements without a text or html representation does not make sense.")

            content = MIMEMultipart('related')
            content.attach(body)
            for inline_part in inline_parts:
                content.attach(inline_part)

        else:
            content = body

        # If there are attachments, everything must be wrapped in multipart/mixed.
        if attachment_parts:
            message = MIMEMultipart('mixed')
            if content is not None:
                message.attach(content)
            for attachment_part in attachment_parts:
                message.attach(attachment_part)
        else:
            message = content

        if message is None:
            raise MailerException("There is not data to sent.")

        ## Assemble mail header
        if to:
            message['To'] = ','.join(to)
            recipients.extend(to)
        if cc:
            message['Cc'] = ','.join(cc)
            recipients.extend(cc)
        if bcc:
            message['Bcc'] = ','.join(bcc)
            recipients.extend(bcc)
        if reply_to:
            message['Reply-To'] = reply_to
        message['From'] = from_addr
        message['Subject'] = subject

        if header:
            for header_key, content in header.items():
                message[header_key] = content

        if not recipients:
            raise MailerException("No recipients defined. to, cc and bcc are empty.")

        return self._conn.sendmail(from_addr, recipients, message.as_string())

if __name__ == "__main__":

    def test():
        mailer = Mailer('mail.example.com', 587)
        mailer.login('test@example.com', '***')

        mailer.send(
            to = [
                '"My dude" <mydude@example.com>'
            ],
            cc = [],
            from_addr = 'Test User <test@example.com>',
            # subject with umlauts
            subject = 'Test Mail äöü',
            # plaintext with newlines
            plaintext = '== Titel ==\nHi',
            # HTML with image
            html = '<h1>Titel</h1>Hi<img src="cid:logo@cloudflare.com"/>',
            # different attachment types
            attachments = [
                # just binary data
                b'datablock',
                # plain text from file in current directory
                {
                    'file': 'README.md',
                    'type': 'text/plain'
                },
                # a named attachment from binary data
                {
                    'name': 'hi.txt',
                    'data' : b'huibuhhh'
                },
                # attachment downloaded from an URL
                {
                    'url': 'https://cdnjs.cloudflare.com/ajax/libs/cookieconsent2/3.0.3/cookieconsent.min.js?v=12'
                }
            ],
            #
            inlines = [
                {
                    'url' : "https://cdnjs.cloudflare.com/logo.svg",
                    'cid' : 'logo@cloudflare.com'
                }
            ]
        )

        mailer.quit()

    test()
