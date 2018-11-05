# tightknot -- Class wrapper around libknot.control
#
# Higher-level transaction management, collecting succes/failure.
#
# From: Rick van Rein <rick@openfortress.nl>


import libknot.control

import os
import sys
import string
import json


#
# This uses the Python library to control Knot DNS.
# Its document includes a decent example:
#
#     Example:
#         import json
#         from libknot.control import *
#     
#         #load_lib("/usr/lib/libknot.so")
#     
#         ctl = KnotCtl()
#         ctl.connect("/var/run/knot/knot.sock")
#     
#         try:
#             ctl.send_block(cmd="conf-begin")
#             resp = ctl.receive_block()
#     
#             ctl.send_block(cmd="conf-set", section="zone", item="domain", data="test")
#             resp = ctl.receive_block()
#     
#             ctl.send_block(cmd="conf-commit")
#             resp = ctl.receive_block()
#     
#             ctl.send_block(cmd="conf-read", section="zone", item="domain")
#             resp = ctl.receive_block()
#             print(json.dumps(resp, indent=4))
#         finally:
#             ctl.send(KnotCtlType.END)
#             ctl.close()
#



class TightKnot:

	"""This class maintains state about Knot transactions:

	   **txn_success** is a boolean that indicates that the
	   current transaction has been successful up to here.

	   **txn_conf** is a boolean indicating if a global
	   configuration transaction has been assigned.

	   **txn_zone** is a set of zones for which a transaction
	   has been opened.

	   GOOD: Can use zone=, owner=, ttl=(str), rtype=, data=
	   GOOD: Could wrap and test if a zone is already locked
	   POOR: Cannot lock zones inside conf.
	   GOOD: Succeeded blocks usually have an inverse.
	   POOR: Need to build trasactions on top of Knot's...
	   POOR: Have to use '@' in owner names, at least for SOA.
	   POOR: Only one control connection at any time.
	   POOR: Maximum timeout does not cause error.
	   GOOD: Maximum timeout can be requested.
	   POOR: May need to keepalive a transaction.
	   POOR: Commit does not always finish a transaction.
	   POOR: Cannot wait for a transaction lock.

	"""

	def __init__ (self, socketpath='/var/run/knot/knot.sock'):
		"""Connect to knotd
		"""
		self.ctl = None
		self.txn_conf = False
		self.txn_zone = set ()
		self.txn_success = True
		self.ctl = libknot.control.KnotCtl ()
		self.ctl.connect (socketpath)
		self.ctl.set_timeout (3600)

	def __del__ (self):
		"""Cleanup the object.  We started __init__ with
		   setup of variables without external calls, so we
		   should be fine there.
		"""
		self.close ()

	def close (self):
		"""Disconnect from knotd; this involves aborting
		   any transactions that may still be open.
		"""
		self.force_abort ()
		if self.ctl is not None:
			self.ctl.send (libknot.control.KnotCtlType.END)
			self.ctl.close ()
		self.ctl = None

	def knot (self, **block):
		"""Run a command block and return the results.
		   This is skipped when the current transaction
		   has already failed.  The result from the
		   operation is returned.  Exceptions thrown by
		   libknot are caught and made into transaction
		   failures, and in such cases None is returned.
		"""
		if not self.txn_success:
			# Already failed; discontinue
			return None
		try:
			print 'KnotControl send', block
			self.ctl.send_block (**block)
			resp = self.ctl.receive_block ()
			print 'KnotControl recv', resp
		except libknot.control.KnotCtlError as lck:
			self.txn_success = False
			sys.stderr.write ('KnotControl exception: %s\n' % (str (lck),))
			resp = None
		return resp

	def try_commit (self):
		"""Attempt to commit the current transactions run by
		   knot.  If there was a failure, abort instead.  The
		   function returns the (overall) success of all
		   actions run through knot().
		"""
		if self.txn_success:
			pass #self.knot (cmd='conf-check')
		for zone in self.txn_zone:
			if self.txn_success:
				pass #self.knot (cmd='zone-check', item=zone)
		if self.txn_success:
			# Success
			conf_cmd = 'conf-commit'
			zone_cmd = 'zone-commit'
		else:
			# Failure
			conf_cmd = 'conf-abort'
			zone_cmd = 'zone-abort'
			self.txn_success = True
		for zone in self.txn_zone:
			self.knot (cmd=zone_cmd, data=zone)
		self.txn_zone = set ()
		if self.txn_conf:
			self.knot (cmd=conf_cmd)
			self.txn_conf = False
		if not self.txn_success:
			raise InternalError ('Unexpected failure during %s and/or %s' % (conf_cmd, zone_cmd))
		return self.txn_success

	def force_abort (self):
		"""Deliberately abort all transactions run against knot.
		   The cause can be anything outside of DNS.
		   
		   After this procedure, the state has been reset, and
		   new transactions may be opened.  With no transactions
		   open, this has no effect.
		   
		   This function also runs when the object is closed,
		   to ensure giving up any locks that were held, and to
		   avoid implicitly committing unfinished data from an
		   unfinished program.
		"""
		self.txn_success = False
		self.try_commit ()

	def have_conf (self):
		"""Lock the conf transaction; this should always
		   be done before any of the zone locks.
		"""
		if self.txn_conf:
			return
		if len (self.txn_zone) > 0:
			raise Exception ('Allocate a configuration lock with or before the first zone lock')
		self.knot (cmd='conf-begin')
		self.txn_conf = True

	def have_zones (self, zones, conf=False):
		"""Use this method to claim locks on a set (or list)
		   of zones.  A single zone may be used too, supplied
		   as a string.  Under the assumption that other knot
		   clients use the same order, namely alphabetic
		   order of lowercase-mapped zone names, there should
		   be no risk of deadlock.  That is under the
		   assumption that any configuration lock is obtained
		   either before or with the first zone.
		  
		   To prevent deadlock, you cannot call this function
		   more than once; you need to supply all the zones
		   at the same time.
		  
		   #TODO# We could allow sequencing for sub-zones?
		"""
		if len (self.txn_zone) > 0:
			raise Exception ('Lock all zones at once to stave off deadlocks')
		if type (zones) == type (''):
			ordered_zones = [zones]
		else:
			ordered_zones = map (string.lower, zones)
			ordered_zones.sort ()
		if conf:
			self.have_conf ()
		for zone in ordered_zones:
			self.knot (cmd='zone-begin', data=zone)
			self.txn_zone.add (zone)

	def add_zone (self, zone):
		"""Add a zone.
		"""
		if not self.txn_conf:
			raise Exception ('Configuration not locked')
		self.knot (cmd='conf-set', section='zone', item='domain', data=zone)

	def del_zone (self, zone):
		"""Delete a zone.
		"""
		if not self.txn_conf:
			raise Exception ('Configuration not locked')
		self.knot (cmd='conf-unset', section='zone', item='domain', data=zone)

	def _rr (self, cmd, zone, owner, ttl, rtype, rdata):
		"""Called with add_del either 'add' or 'del' to
		   add or delete resource record data in the zone.
		"""
		if not self.txn_success:
			return False
		if not zone in self.txn_zone:
			raise Exception ('Zone not locked: %s' % zone)
			self.txn_success = False
			return False
		self.knot (cmd=cmd, zone=zone, owner=owner, ttl=ttl, rtype=rtype, data=rdata)
		return False

	def add_rr (self, zone, owner, ttl, rtype, rdata):
		"""Add the given resource record.
		"""
		self._rr ('zone-set', zone, owner, ttl, rtype, rdata)

	def del_rr (self, zone, owner, ttl, rtype, rdata):
		"""Delete the given resource record.
		"""
		self._rr ('zone-unset', zone, owner, ttl, rtype, rdata)

	def _knotc_shell (self, knotc_subcommand, expect_ok=True):
		"""Send a command to knotc, using the commandline.
		   This is far from efficient, considering that the
		   Python API could theoretically do it too.  The
		   reality however, is that this API is still not
		   fully developed (or documented) so it is not yet
		   usable.  This should be seen as a temporary
		   mechanism, which we hope to remove at some point.
		   This mechanism collects errors to help firm up
		   the transaction model, that's the main reason
		   why it is here.
		"""
		try:
			knotc = os.popen ('knotc ' + knotc_subcommand, 'r')
			if expect_ok:
				output = knotc.read ()
				if output.strip () != 'OK':
					sys.stderr.write ('Shell output is not "OK" but "%s"\n' % (output,))
					self.txn_success = False
			exitval = knotc.close ()
			if exitval is not None:
				sys.stderr.write ('Shell exited with %d\n' % (exitval,))
				self.txn_success = False
		except Exception as e:
			sys.stderr.write ('Shell raised %s: %s\n' % (e, type (e)))
			self.txn_success = False


if False:

	#TODO# BE LAZY, BOOT KNOTD FROM HERE
	import os
	os.system ("/etc/init.d/knot start")
	import time
	# time.sleep (2)


	#TODO# BE LAZY, START THINGS FROM HERE

	knot = None
	try:
		knot = KnotControl ()

		# time.sleep (5)

		knot.have_conf ()
		knot.knot (cmd='conf-set', section='zone', item='domain', data='orvelte.nep')
		knot.try_commit ()

		knot_have_zones ('orvelte.nep')
		knot.add_rr ('orvelte.nep', '@', '3600', 'SOA',
			'ns1.orvelte.nep. admin.orvelte.nep. 0 10800 3600 1814400 3600')
		knot.add_rr ('orvelte.nep', '@', '3600', 'RP',
			'admin.orvelte.nep .')
		knot.try_commit ()

		# time.sleep (4)

		knot.have_conf ()
		knot.knot (cmd='conf-unset', section='zone', item='domain', data='orvelte.nep')
		knot.try_commit ()

	except Exception as e:
		print 'EXCEPTION:', e

	finally:
		if knot is not None:
			knot.close ()
			knot = None

