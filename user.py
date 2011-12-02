import logging
from functools import lru_cache
import datetime

import pymongo.errors

import models
from models import connection
import config
from greenlets import Welcome
from misc import *

logger = logging.getLogger(__name__)

class UserMixin:
  # _cached_jid: the corresponding jid cached in _cached_user
  _cached_jid = _cached_user = None
  current_jid = current_user = None
  _cached_allusers = None

  @property
  def current_user(self):
    if self._cached_jid == self.current_jid:
      return self._cached_user

    if self.current_jid is None:
      return

    plainjid = str(self.current_jid.bare())
    user = connection.User.one({'jid': plainjid})

    # not in database
    if user is None:
      user = self.db_add_user(plainjid)
      Welcome(self.current_jid, self, use_roster_nick=True)

    self._cached_jid = self.current_jid
    self._cached_user = user
    return user

  @property
  def allusers(self):
    if self._cached_allusers is not None:
      return self._cached_allusers

    allusers = {u['jid'] for u in connection.User.find({
      'stop_until': datetime.datetime.utcnow()
    }, ['jid'])}

    self._cached_allusers = allusers
    return allusers

  def handle_userjoin_before(self):
    # TODO do block check here
    # may invoke twice
    return True

  def db_add_user(self, plainjid):
    '''
    add new user to database, return the added user; if alreadly exists, query
    and return it
    '''
    u = connection.User()
    u.jid = plainjid
    if plainjid == config.root:
      u.flag = PERM_USER | PERM_GPADMIN | PERM_SYSADMIN
    try:
      u.save()
    except pymongo.errors.DuplicateKeyError:
      u = connection.User.one({'jid': plainjid})
    return u

  def set_user_nick(self, *args, **kwargs):
    '''set sender's nick in database

    return the old `User` document, raise ValueError if duplicate
    use `increase` tells if this is an auto action so that the counter should
    not be increased

    This will reset the nick cache.
    '''
    try:
      return self._set_user_nick(*args, **kwargs)['nick']
    except TypeError: #None
      pass

  def set_self_nick(self, nick):
    '''set sender's nick in database

    return the old nick or None
    This will reset the nick cache.
    '''
    jid = str(self.current_jid.bare())
    user = self._set_user_nick(jid, nick)
    return user['nick']

  def _set_user_nick(self, plainjid, nick, increase=True):
    '''set a user's nick in database

    return the old `User` document, raise ValueError if duplicate
    `increase` tells if this is an auto action so that the counter should not
    be increased

    This will reset the nick cache.
    '''
    models.validate_nick(nick)
    if self.nick_exists(nick):
      raise ValueError(_('duplicate nick name: %s') % nick)

    self.user_get_nick.cache_clear()
    update = {
      '$set': {
        'nick': nick,
        'nick_lastchange': datetime.datetime.utcnow(),
      }
    }
    if increase:
      update['$inc'] = {'nick_changes': 1}

    # XXX: mongokit currently does not support find_and_modify
    return connection.User.collection.find_and_modify(
      {'jid': plainjid}, update
    )

  @lru_cache()
  def user_get_nick(self, plainjid):
    '''get a user's nick
    
    The result is cached so if any of the users's nicks change, call `cache_clear()`.
    Fallback to `self.get_name` if not found in database'''
    u = connection.User.one({'jid': plainjid}, ['nick'])
    nick = u.nick if u else None
    if nick is None:
      #fallback
      nick = self.get_name(plainjid)
    return nick

  def nick_exists(self, nick):
    return connection.User.find_one({'nick': nick}, {}) is not None

  def get_user_by_nick(self, nick):
    '''returns a `User` object
    
    nick should not be `None` or an arbitrary one will be returned'''
    return connection.User.find_one({'nick': nick})

  def handle_userjoin(self, action):
    '''add the user to database and say Welcome'''
    # TODO: 根据 action 区别处理
    plainjid = str(self.current_jid.bare())

    self._cached_jid = None
    u = self.db_add_user(plainjid)
    self._cached_allusers.add(plainjid)

    Welcome(self.current_jid, self)
    logger.info('%s joined', plainjid)

  def handle_userleave(self, action):
    '''user has left, delete the user from database'''
    # TODO: 根据 action 区别处理
    self._cached_allusers.remove(self.current_user.jid)
    self.current_user.delete()
    self._cached_jid = None

    logger.info('%s left', self.current_jid)