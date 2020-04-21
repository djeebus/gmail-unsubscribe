import setuptools

from distutils.core import setup


setup(
    name='gmail-unsubscribe',
    author='Joe Lombrozo',
    author_email='joe@djeebus.net',
    packages=setuptools.find_packages(),
    install_requires=[
        'beautifulsoup4',
        'click',
        'google-api-python-client',
        'oauth2client',
        'urlfetch',
    ],
)
