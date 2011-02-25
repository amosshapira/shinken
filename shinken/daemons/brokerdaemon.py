#!/usr/bin/env python
#Copyright (C) 2009-2010 :
#    Gabes Jean, naparuba@gmail.com
#    Gerhard Lausser, Gerhard.Lausser@consol.de
#    Gregory Starck, g.starck@gmail.com
#    Hartmut Goebel, h.goebel@goebel-consult.de
#
#This file is part of Shinken.
#
#Shinken is free software: you can redistribute it and/or modify
#it under the terms of the GNU Affero General Public License as published by
#the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.
#
#Shinken is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU Affero General Public License for more details.
#
#You should have received a copy of the GNU Affero General Public License
#along with Shinken.  If not, see <http://www.gnu.org/licenses/>.

import os
import sys
import time
import traceback

from multiprocessing import active_children
from Queue import Empty

from shinken.satellite import BaseSatellite, IForArbiter

from shinken.util import to_int, sort_by_ids
from shinken.log import logger

import shinken.pyro_wrapper as pyro
from shinken.pyro_wrapper import Pyro

from shinken.external_command import ExternalCommand


# Our main APP class
class Broker(BaseSatellite):

    properties = BaseSatellite.properties.copy()
    properties.update({
        'pidfile':   { 'default': '/usr/local/shinken/var/brokerd.pid', 'pythonize': None, 'path': True },
        'port':      { 'default': '7772', 'pythonize': to_int },
        'local_log': { 'default': '/usr/local/shinken/var/brokerd.log', 'pythonize': None, 'path': True },
    })


    def __init__(self, config_file, is_daemon, do_replace, debug, debug_file):
        
        super(Broker, self).__init__('broker', config_file, is_daemon, do_replace, debug, debug_file)

        # Our arbiters
        self.arbiters = {}

        # Our pollers and reactionners
        self.pollers = {}
        self.reactionners = {}

        # Modules are load one time
        self.have_modules = False

        # Can have a queue of external_commands give by modules
        # will be taken by arbiter to process
        self.external_commands = []

        # All broks to manage
        self.broks = [] # broks to manage
        # broks raised this turn and that need to be put in self.broks
        self.broks_internal_raised = []
        

    # Schedulers have some queues. We can simplify call by adding
    # elements into the proper queue just by looking at their type
    # Brok -> self.broks
    # TODO : better tag ID?
    # External commands -> self.external_commands
    def add(self, elt):
        cls_type = elt.__class__.my_type
        if cls_type == 'brok':
            # For brok, we TAG brok with our instance_id
            elt.data['instance_id'] = 0
            self.broks_internal_raised.append(elt)
            return
        elif cls_type == 'externalcommand':
            print "Adding in queue an external command", ExternalCommand.__dict__
            self.external_commands.append(elt)


    # Get teh good tabs for links by the kind. If unknown, return None
    def get_links_from_type(self, type):
        t = {'scheduler' : self.schedulers, 'arbiter' : self.arbiters, \
             'poller' : self.pollers, 'reactionner' : self.reactionners}
        if type in t :
            return t[type]
        return None


    # Call by arbiter to get our external commands
    def get_external_commands(self):
        res = self.external_commands
        self.external_commands = []
        return res


    # Check if we do not connect to ofthen to this
    def is_connexion_try_too_close(self, elt):
        now = time.time()
        last_connexion = elt['last_connexion']
        if now - last_connexion < 5:
            return  True
        return False


    # initialise or re-initialise connexion with scheduler or
    # arbiter if type == arbiter
    def pynag_con_init(self, id, type='scheduler'):
            # Get teh good links tab for looping..
        links = self.get_links_from_type(type)
        if links == None:
            logger.log('DBG: Type unknown for connexion! %s' % type)
            return

        if type == 'scheduler':
            # If sched is not active, I do not try to init
            # it is just useless
            is_active = links[id]['active']
            if not is_active:
                return

        # If we try to connect too much, we slow down our tests
        if self.is_connexion_try_too_close(links[id]):
            return

        # Ok, we can now update it
        links[id]['last_connexion'] = time.time()

        # DBG: print "Init connexion with", links[id]['uri']
        running_id = links[id]['running_id']
        # DBG: print "Running id before connexion", running_id
        uri = links[id]['uri']
        links[id]['con'] = Pyro.core.getProxyForURI(uri)

        try:
                # intial ping must be quick
            pyro.set_timeout(links[id]['con'], 5)
            links[id]['con'].ping()
            new_run_id = links[id]['con'].get_running_id()
            # data transfert can be longer
            pyro.set_timeout(links[id]['con'], 120)

            # The schedulers have been restart : it has a new run_id.
            # So we clear all verifs, they are obsolete now.
            if new_run_id != running_id:
                print "[%s] New running id for the %s %s : %s (was %s)" % (self.name, type, links[id]['name'], new_run_id, running_id)
                links[id]['broks'].clear()
                # we must ask for a enw full broks if
                # it's a scheduler
                if type == 'scheduler':
                    print "[%s] I ask for a broks generation to the scheduler %s" % (self.name, links[id]['name'])
                    links[id]['con'].fill_initial_broks()
            # else:
            #     print "I do nto ask for brok generation"
            links[id]['running_id'] = new_run_id
        except Pyro.errors.ProtocolError, exp:
            logger.log("[%s] Connexion problem to the %s %s : %s" % (self.name, type, links[id]['name'], str(exp)))
            links[id]['con'] = None
            return
        except Pyro.errors.NamingError, exp:
            logger.log("[%s] the %s '%s' is not initilised : %s" % (self.name, type, links[id]['name'], str(exp)))
            links[id]['con'] = None
            return
        except KeyError , exp:
            logger.log("[%s] the %s '%s' is not initilised : %s" % (self.name, type, links[id]['name'], str(exp)))
            links[id]['con'] = None
            traceback.print_stack()
            return
        except Pyro.errors.CommunicationError, exp:
            logger.log("[%s] the %s '%s' got CommunicationError : %s" % (self.name, type, links[id]['name'], str(exp)))
            links[id]['con'] = None
            return

        logger.log("[%s] Connexion OK to the %s %s" % (self.name, type, links[id]['name']))


    # Get a brok. Our role is to put it in the modules
    # THEY MUST DO NOT CHANGE data of b !!!
    # REF: doc/broker-modules.png (4-5)
    def manage_brok(self, b):
        to_del = []
        # Call all modules if they catch the call
        for mod in self.modules_manager.get_internal_instances():
            try:
                mod.manage_brok(b)
            except Exception , exp:
                print exp.__dict__
                logger.log("[%s] Warning : The mod %s raise an exception: %s, I kill it" % (self.name, mod.get_name(),str(exp)))
                logger.log("[%s] Exception type : %s" % (self.name, type(exp)))
                logger.log("Back trace of this kill: %s" % (traceback.format_exc()))
                to_del.append(mod)
        # Now remove mod that raise an exception
        self.modules_manager.clear_instances(to_del)


    # Add broks (a tab) to different queues for
    # internal and external modules
    def add_broks_to_queue(self, broks):
        # Ok now put in queue brocks for manage by
        # internal modules
        self.broks.extend(broks)

    # Each turn we get all broks from
    # self.broks_internal_raised and we put them in
    # self.broks
    def interger_internal_broks(self):
        self.add_broks_to_queue(self.broks_internal_raised)
        self.broks_internal_raised = []

    # Get 'objects' from external modules
    # from now nobody use it, but it can be useful
    # for a moduel like livestatus to raise external
    # commandsfor example
    def get_objects_from_from_queues(self):
        for f in self.modules_manager.get_external_from_queues():
            full_queue = True
            while full_queue:
                try:
                    o = f.get(block=False)
                    self.add(o)
                except Empty :
                    full_queue = False

    # We get new broks from schedulers
    # REF: doc/broker-modules.png (2)
    def get_new_broks(self, type='scheduler'):
            # Get teh good links tab for looping..
        links = self.get_links_from_type(type)
        if links == None:
            logger.log('DBG: Type unknown for connexion! %s' % type)
            return

        # We check for new check in each schedulers and put
        # the result in new_checks
        for sched_id in links:
            try:
                con = links[sched_id]['con']
                if con is not None: # None = not initilized
                    tmp_broks = con.get_broks()
                    for b in tmp_broks.values():
                        b.instance_id = links[sched_id]['instance_id']

                    # Ok, we can add theses broks to our queues
                    self.add_broks_to_queue(tmp_broks.values())

                else: # no con? make the connexion
                    self.pynag_con_init(sched_id, type=type)
            # Ok, con is not know, so we create it
            except KeyError , exp:
                print exp
                self.pynag_con_init(sched_id, type=type)
            except Pyro.errors.ProtocolError , exp:
                logger.log("[%s] Connexion problem to the %s %s : %s" % (self.name, type, links[sched_id]['name'], str(exp)))
                links[sched_id]['con'] = None
            # scheduler must not #be initialized
            except AttributeError , exp:
                logger.log("[%s] The %s %s should not be initialized : %s" % (self.name, type, links[sched_id]['name'], str(exp)))
            # scheduler must not have checks
            except Pyro.errors.NamingError , exp:
                logger.log("[%s] The %s %s should not be initialized : %s" % (self.name, type, links[sched_id]['name'], str(exp)))
            except Pyro.errors.ConnectionClosedError , exp:
                logger.log("[%s] Connexion problem to the %s %s : %s" % (self.name, type, links[sched_id]['name'], str(exp)))
                links[sched_id]['con'] = None
            #  What the F**k? We do not know what happenned,
            # so.. bye bye :)
            except Exception,x:
                print x.__class__
                print x.__dict__
                logger.log(str(x))
                logger.log(''.join(Pyro.util.getPyroTraceback(x)))
                sys.exit(1)

    # modules can have process, and they can die
    def check_and_del_zombie_modules(self):
        # Active children make a join with every one, useful :)
        act = active_children()
        self.modules_manager.check_alive_instances()

    def do_stop(self):
        act = active_children()
        for a in act:
            a.terminate()
            a.join(1)
        super(Broker, self).do_stop()
        
        
    def setup_new_conf(self):
        conf = self.new_conf
        self.new_conf = None
        self.cur_conf = conf
        # Got our name from the globals
        if 'broker_name' in conf['global']:
            name = conf['global']['broker_name']
        else:
            name = 'Unnamed broker'
        self.name = name
        self.log.load_obj(self, name)

        print "[%s] Sending us configuration %s" % (self.name, conf)
        # If we've got something in the schedulers, we do not
        # want it anymore
        # self.schedulers.clear()
        for sched_id in conf['schedulers']:
            # Must look if we already have it to do nto overdie our broks
            already_got = sched_id in self.schedulers
            if already_got:
                broks = self.schedulers[sched_id]['broks']
                running_id = self.schedulers[sched_id]['running_id']
            else:
                broks = {}
                running_id = 0
            s = conf['schedulers'][sched_id]
            self.schedulers[sched_id] = s
            uri = pyro.create_uri(s['address'], s['port'], 'Broks', self.use_ssl)
            self.schedulers[sched_id]['uri'] = uri
            self.schedulers[sched_id]['broks'] = broks
            self.schedulers[sched_id]['instance_id'] = s['instance_id']
            self.schedulers[sched_id]['running_id'] = running_id
            self.schedulers[sched_id]['active'] = s['active']
            self.schedulers[sched_id]['last_connexion'] = 0


        logger.log("[%s] We have our schedulers : %s " % (self.name, self.schedulers))

        # Now get arbiter
        for arb_id in conf['arbiters']:
            # Must look if we already have it
            already_got = arb_id in self.arbiters
            if already_got:
                broks = self.arbiters[arb_id]['broks']
            else:
                broks = {}
            a = conf['arbiters'][arb_id]
            self.arbiters[arb_id] = a
            uri = pyro.create_uri(a['address'], a['port'], 'Broks', self.use_ssl)
            self.arbiters[arb_id]['uri'] = uri
            self.arbiters[arb_id]['broks'] = broks
            self.arbiters[arb_id]['instance_id'] = 0 # No use so all to 0
            self.arbiters[arb_id]['running_id'] = 0
            self.arbiters[arb_id]['last_connexion'] = 0

            # We do not connect to the arbiter. To connexion hang

        logger.log("[%s] We have our arbiters : %s " % (self.name, self.arbiters))

        # Now for pollers
        for pol_id in conf['pollers']:
            # Must look if we already have it
            already_got = pol_id in self.pollers
            if already_got:
                broks = self.pollers[pol_id]['broks']
                running_id = self.schedulers[sched_id]['running_id']
            else:
                broks = {}
                running_id = 0
            p = conf['pollers'][pol_id]
            self.pollers[pol_id] = p
            uri = pyro.create_uri(p['address'], p['port'], 'Broks', self.use_ssl)
            self.pollers[pol_id]['uri'] = uri
            self.pollers[pol_id]['broks'] = broks
            self.pollers[pol_id]['instance_id'] = 0 # No use so all to 0
            self.pollers[pol_id]['running_id'] = running_id
            self.pollers[pol_id]['last_connexion'] = 0

#                    #And we connect to it
#                    self.app.pynag_con_init(pol_id, 'poller')

        logger.log("[%s] We have our pollers : %s" % (self.name, self.pollers))

        # Now reactionners
        for rea_id in conf['reactionners'] :
            # Must look if we already have it
            already_got = rea_id in self.reactionners
            if already_got:
                broks = self.reactionners[rea_id]['broks']
                running_id = self.schedulers[sched_id]['running_id']
            else:
                broks = {}
                running_id = 0

            r = conf['reactionners'][rea_id]
            self.reactionners[rea_id] = r
            uri = pyro.create_uri(r['address'], r['port'], 'Broks', self.use_ssl)
            self.reactionners[rea_id]['uri'] = uri
            self.reactionners[rea_id]['broks'] = broks
            self.reactionners[rea_id]['instance_id'] = 0 # No use so all to 0
            self.reactionners[rea_id]['running_id'] = running_id
            self.reactionners[rea_id]['last_connexion'] = 0

#                    #And we connect to it
#                    self.app.pynag_con_init(rea_id, 'reactionner')

        logger.log("[%s] We have our reactionners : %s" % (self.name, self.reactionners))

        if not self.have_modules:
            self.modules = mods = conf['global']['modules']
            self.have_modules = True
            logger.log("[%s] We received modules %s " % (self.name,  mods))

        # Set our giving timezone from arbiter
        use_timezone = conf['global']['use_timezone']
        if use_timezone != 'NOTSET':
            logger.log("[%s] Setting our timezone to" % (self.name, use_timezone))
            os.environ['TZ'] = use_timezone
            time.tzset()
        
        # Connexion init with Schedulers
        for sched_id in self.schedulers:
            self.pynag_con_init(sched_id, type='scheduler')

        for pol_id in self.pollers:
            self.pynag_con_init(pol_id, type='poller')

        for rea_id in self.reactionners:
            self.pynag_con_init(rea_id, type='reactionner')
        

    def do_loop_turn(self):
        print "Begin Loop : manage broks", len(self.broks)
        # Begin to clean modules
        self.check_and_del_zombie_modules()

        # Now we check if arbiter speek to us in the pyro_daemon.
        # If so, we listen for it
        # When it push us conf, we reinit connexions
        self.watch_for_new_conf(0.0)
        if self.new_conf:
            self.setup_new_conf()

        # Maybe the last loop we raised some broks internally
        # we should interger them in broks
        self.interger_internal_broks()

        # And from schedulers
        self.get_new_broks(type='scheduler')
        # And for other satellites
        self.get_new_broks(type='poller')
        self.get_new_broks(type='reactionner')

        # Sort the brok list by id
        self.broks.sort(sort_by_ids)

        # and for external queues
        # REF: doc/broker-modules.png (3)
        for b in self.broks:
            # if b.type != 'log':
            #     print "Broker : put brok id : %d" % b.id
            for q in self.modules_manager.get_external_to_queues():
                q.put(b)

        # We must had new broks at the end of the list, so we reverse the list
        self.broks.reverse()

        start = time.time()
        while len(self.broks) != 0:
            now = time.time()
            # Do not 'manage' more than 1s, we must get new broks
            # every 1s
            if now - start > 1:
                break

            b = self.broks.pop()
            # Ok, we can get the brok, and doing something with it
            # REF: doc/broker-modules.png (4-5)
            self.manage_brok(b)

            nb_broks = len(self.broks)

            # Ok we manage brok, but we still want to listen to arbiter
            self.watch_for_new_conf(0.0)

            # if we got new broks here from arbiter, we should breack the loop
            # because such broks will not be managed by the
            # external modules before this loop (we pop them!)
            if len(self.broks) != nb_broks:
                break

        # Maybe external modules raised 'objets'
        # we should get them
        self.get_objects_from_from_queues()

        # Maybe we do not have something to do, so we wait a little
        if len(self.broks) == 0:
            # print "watch new conf 1 : begin", len(self.broks)
            self.watch_for_new_conf(1.0)
            # print "get enw broks watch new conf 1 : end", len(self.broks)


    #  Main function, will loop forever
    def main(self):
        
        self.load_config_file()
        
        for line in self.get_header():
            self.log.log(line)

        logger.log("[Broker] Using working directory : %s" % os.path.abspath(self.workdir))
        
        self.do_daemon_init_and_start()

        self.uri2 = self.pyro_daemon.register(self.interface.pyro_obj, "ForArbiter")
        print "The Arbtier uri it at", self.uri2

        #  We wait for initial conf
        self.wait_for_initial_conf()
        if not self.new_conf:
            return

        self.setup_new_conf()

        self.modules_manager.set_modules(self.modules)
        self.do_load_modules()

        # Do the modules part, we have our modules in self.modules
        # REF: doc/broker-modules.png (1)


        # Now the main loop
        self.do_mainloop()
