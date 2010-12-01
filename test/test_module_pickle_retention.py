#!/usr/bin/env python2.6
#Copyright (C) 2009-2010 :
#    Gabes Jean, naparuba@gmail.com
#    Gerhard Lausser, Gerhard.Lausser@consol.de
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


#
# This file is used to test reading and processing of config files
#

#It's ugly I know....
import os
from shinken_test import *
sys.path.append("../shinken/modules")
from pickle_retention_file_scheduler import *

class TestConfig(ShinkenTest):
    #setUp is in shinken_test

    #Change ME :)
    def test_host_perfdata(self):
        print self.conf.modules
        #get our modules
        mod = None
        mod = Module({'type' : 'pickle_retention_file', 'module_name' : 'PickleRetention', 'path' : '/tmp/retention-test.dat'})

        try :
            os.unlink(mod.path)
        except :
            pass

        sl = get_instance(mod)
        print "Instance", sl
        #Hack here :(
        sl.properties = {}
        sl.properties['to_queue'] = None
        sl.init()
        l = logger
        #updte the hosts and service in the scheduler in the retentino-file
        sl.update_retention_objects(self.sched, l)
        
        #Now we change thing
        svc = self.sched.hosts.find_by_name("test_host_0")
        self.assert_(svc.state == 'PENDING')
        print "State", svc.state
        svc.state = 'UP' #was PENDING in the save time

        r = sl.load_retention_objects(self.sched, l)
        self.assert_(r == True)

        #search if the host is not changed by the loading thing
        svc2 = self.sched.hosts.find_by_name("test_host_0")
        self.assert_(svc == svc2)

        self.assert_(svc.state == 'PENDING')

        #Ok, we can delete the retention file
        os.unlink(mod.path)



if __name__ == '__main__':
    unittest.main()
