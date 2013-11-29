# Copyright (c) 2013 Hortonworks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from savanna.plugins.general import exceptions as ex
from savanna.swift import swift_helper as h


def create_service(name):
    for cls in Service.__subclasses__():
        if cls.get_service_id() == name:
            return cls()
    # no subclass found, return service base class
    return Service(name)


class Service(object):
    def __init__(self, name):
        self.name = name
        self.configurations = set(['global', 'core-site'])
        self.components = []
        self.users = []
        self.deployed = False

    def add_component(self, component):
        self.components.append(component)

    def add_user(self, user):
        self.users.append(user)

    def validate(self, cluster_spec, cluster):
        pass

    def finalize_configuration(self, cluster_spec):
        pass

    def register_user_input_handlers(self, ui_handlers):
        pass

    def register_service_urls(self, cluster_spec, url_info):
        return url_info

    def pre_service_start(self, cluster_spec, ambari_info, started_services):
        pass

    def finalize_ng_components(self, cluster_spec):
        pass

    def is_user_template_component(self, component):
        return True

    def is_mandatory(self):
        return False

    def _replace_config_token(self, cluster_spec, token, value, props):
        for config_name, props in props.iteritems():
            config = cluster_spec.configurations[config_name]
            for prop in props:
                config[prop] = config[prop].replace(token, value)

    def _get_common_paths(self, node_groups):
        if len(node_groups) == 1:
            paths = node_groups[0].storage_paths
        else:
            sets = [set(ng.storage_paths) for ng in node_groups]
            paths = list(set.intersection(*sets))

        if len(paths) > 1 and '/mnt' in paths:
            paths.remove('/mnt')

        return paths

    def _generate_storage_path(self, storage_paths, path):
        return ",".join([p + path for p in storage_paths])


class HdfsService(Service):
    def __init__(self):
        super(HdfsService, self).__init__(HdfsService.get_service_id())
        self.configurations.add('hdfs-site')

    @classmethod
    def get_service_id(cls):
        return 'HDFS'

    def validate(self, cluster_spec, cluster):
        # check for a single NAMENODE
        count = cluster_spec.get_deployed_node_group_count('NAMENODE')
        if count != 1:
            raise ex.InvalidComponentCountException('NAMENODE', 1, count)

    def finalize_configuration(self, cluster_spec):
        nn_hosts = cluster_spec.determine_component_hosts('NAMENODE')
        if nn_hosts:
            props = {'core-site': ['fs.default.name'],
                     'hdfs-site': ['dfs.http.address', 'dfs.https.address']}
            self._replace_config_token(
                cluster_spec, '%NN_HOST%', nn_hosts.pop().fqdn, props)

        snn_hosts = cluster_spec.determine_component_hosts(
            'SECONDARY_NAMENODE')
        if snn_hosts:
            props = {'hdfs-site': ['dfs.secondary.http.address']}
            self._replace_config_token(
                cluster_spec, '%SNN_HOST%', snn_hosts.pop().fqdn, props)

        # add swift properties to configuration
        core_site_config = cluster_spec.configurations['core-site']
        for prop in self._get_swift_properties():
            core_site_config[prop['name']] = prop['value']

        # process storage paths to accommodate ephemeral or cinder storage
        nn_ng = cluster_spec.get_node_groups_containing_component(
            'NAMENODE')[0]
        dn_node_groups = cluster_spec.get_node_groups_containing_component(
            'DATANODE')
        common_paths = []
        if dn_node_groups:
            common_paths = self._get_common_paths(dn_node_groups)
        hdfs_site_config = cluster_spec.configurations['hdfs-site']
        global_config = cluster_spec.configurations['global']
        hdfs_site_config['dfs.name.dir'] = self._generate_storage_path(
            nn_ng.storage_paths, '/hadoop/hdfs/namenode')
        global_config['dfs_name_dir'] = self._generate_storage_path(
            nn_ng.storage_paths, '/hadoop/hdfs/namenode')
        if common_paths:
            hdfs_site_config['dfs.data.dir'] = self._generate_storage_path(
                common_paths, '/hadoop/hdfs/data')
            global_config['dfs_data_dir'] = self._generate_storage_path(
                common_paths, '/hadoop/hdfs/data')

    def register_service_urls(self, cluster_spec, url_info):
        namenode_ip = cluster_spec.determine_component_hosts(
            'NAMENODE').pop().management_ip

        #TODO(jspeidel): Don't hard code port
        url_info['HDFS'] = {
            'Web UI': 'http://%s:50070' % namenode_ip,
            'NameNode': 'hdfs://%s:8020' % namenode_ip
        }
        return url_info

    def is_mandatory(self):
        return True

    def _get_swift_properties(self):
        return h.get_swift_configs()


class MapReduceService(Service):
    def __init__(self):
        super(MapReduceService, self).__init__(
            MapReduceService.get_service_id())
        self.configurations.add('mapred-site')

    @classmethod
    def get_service_id(cls):
        return 'MAPREDUCE'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('JOBTRACKER')
        if count != 1:
            raise ex.InvalidComponentCountException('JOBTRACKER', 1, count)

        count = cluster_spec.get_deployed_node_group_count('TASKTRACKER')
        if not count:
            raise ex.InvalidComponentCountException(
                'TASKTRACKER', '> 0', count)

    def finalize_configuration(self, cluster_spec):
        jt_hosts = cluster_spec.determine_component_hosts('JOBTRACKER')
        if jt_hosts:
            props = {'mapred-site': ['mapred.job.tracker',
                                     'mapred.job.tracker.http.address',
                                     'mapreduce.history.server.http.address']}

            self._replace_config_token(
                cluster_spec, '%JT_HOST%', jt_hosts.pop().fqdn, props)

        # process storage paths to accommodate ephemeral or cinder storage
        # NOTE:  mapred.system.dir is an HDFS namespace path (not a filesystem
        # path) so the default path should suffice
        tt_node_groups = cluster_spec.get_node_groups_containing_component(
            'TASKTRACKER')
        if tt_node_groups:
            mapred_site_config = cluster_spec.configurations['mapred-site']
            global_config = cluster_spec.configurations['global']
            common_paths = self._get_common_paths(tt_node_groups)
            mapred_site_config['mapred.local.dir'] = \
                self._generate_storage_path(common_paths, '/hadoop/mapred')
            global_config['mapred_local_dir'] = self._generate_storage_path(
                common_paths, '/hadoop/mapred')

    def register_service_urls(self, cluster_spec, url_info):
        jobtracker_ip = cluster_spec.determine_component_hosts(
            'JOBTRACKER').pop().management_ip

        #TODO(jspeidel): Don't hard code port
        url_info['MapReduce'] = {
            'Web UI': 'http://%s:50030' % jobtracker_ip,
            'JobTracker': '%s:50300' % jobtracker_ip
        }
        return url_info

    def is_mandatory(self):
        return True


class HiveService(Service):
    def __init__(self):
        super(HiveService, self).__init__(HiveService.get_service_id())
        self.configurations.add('hive-site')

    @classmethod
    def get_service_id(cls):
        return 'HIVE'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('HIVE_SERVER')
        if count != 1:
            raise ex.InvalidComponentCountException('HIVE_SERVER', 1, count)

    def finalize_configuration(self, cluster_spec):
        hive_servers = cluster_spec.determine_component_hosts('HIVE_SERVER')
        if hive_servers:
            props = {'global': ['hive_hostname'],
                     'core-site': ['hadoop.proxyuser.hive.hosts'],
                     'hive-site': ['javax.jdo.option.ConnectionURL']}
            self._replace_config_token(
                cluster_spec, '%HIVE_HOST%', hive_servers.pop().fqdn, props)

        hive_ms = cluster_spec.determine_component_hosts('HIVE_METASTORE')
        if hive_ms:
            self._replace_config_token(
                cluster_spec, '%HIVE_METASTORE_HOST%', hive_ms.pop().fqdn,
                {'hive-site': ['hive.metastore.uris']})

        hive_mysql = cluster_spec.determine_component_hosts('MYSQL_SERVER')
        if hive_mysql:
            self._replace_config_token(
                cluster_spec, '%HIVE_MYSQL_HOST%', hive_mysql.pop().fqdn,
                {'global': ['hive_jdbc_connection_url']})

    def register_user_input_handlers(self, ui_handlers):
        ui_handlers['hive-site/javax.jdo.option.ConnectionUserName'] =\
            self._handle_user_property_metastore_user
        ui_handlers['hive-site/javax.jdo.option.ConnectionPassword'] = \
            self._handle_user_property_metastore_pwd

    def _handle_user_property_metastore_user(self, user_input, configurations):
        hive_site_config_map = configurations['hive-site']
        hive_site_config_map['javax.jdo.option.ConnectionUserName'] = \
            user_input.value
        global_config_map = configurations['global']
        global_config_map['hive_metastore_user_name'] = user_input.value

    def _handle_user_property_metastore_pwd(self, user_input, configurations):
        hive_site_config_map = configurations['hive-site']
        hive_site_config_map['javax.jdo.option.ConnectionPassword'] = \
            user_input.value
        global_config_map = configurations['global']
        global_config_map['hive_metastore_user_passwd'] = user_input.value

    def finalize_ng_components(self, cluster_spec):
        hive_ng = cluster_spec.get_node_groups_containing_component(
            'HIVE_SERVER')[0]
        components = hive_ng.components
        if not cluster_spec.get_deployed_node_group_count('HIVE_METASTORE'):
            components.append('HIVE_METASTORE')
        if not cluster_spec.get_deployed_node_group_count('MYSQL_SERVER'):
            components.append('MYSQL_SERVER')
        if not cluster_spec.get_deployed_node_group_count('ZOOKEEPER_SERVER'):
            zk_service = next(service for service in cluster_spec.services
                              if service.name == 'ZOOKEEPER')
            zk_service.deployed = True
            components.append('ZOOKEEPER_SERVER')

    def pre_service_start(self, cluster_spec, ambari_info, started_services):
        # this code is needed because of a bug in Ambari where hdfs dir's
        # are only created at NN initial startup.  Remove this code when
        # the bug is fixed in Ambari.
        if 'HDFS' not in started_services:
            return

        # get any instance
        with cluster_spec.servers[0].remote as r:
            r.execute_command('su -c "hadoop fs -mkdir /user/hive" '
                              '-s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chown -R '
                              'hive:hdfs /user/hive" -s /bin/sh hdfs')

            r.execute_command('su -c "hadoop fs -mkdir /apps/hive" '
                              '-s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chmod -R 755 /apps/hive" '
                              '-s /bin/sh hdfs')

            r.execute_command('su -c "hadoop fs -mkdir /apps/hive/warehouse" '
                              '-s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chown -R hive:hdfs '
                              '/apps/hive/warehouse" -s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chmod -R 777 '
                              '/apps/hive/warehouse" -s /bin/sh hdfs')


class WebHCatService(Service):
    def __init__(self):
        super(WebHCatService, self).__init__(WebHCatService.get_service_id())
        self.configurations.add('webhcat-site')

    @classmethod
    def get_service_id(cls):
        return 'WEBHCAT'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('WEBHCAT_SERVER')
        if count != 1:
            raise ex.InvalidComponentCountException('WEBHCAT_SERVER', 1, count)

    def finalize_configuration(self, cluster_spec):
        webhcat_servers = cluster_spec.determine_component_hosts(
            'WEBHCAT_SERVER')
        if webhcat_servers:
            self._replace_config_token(
                cluster_spec, '%WEBHCAT_HOST%', webhcat_servers.pop().fqdn,
                {'core-site': ['hadoop.proxyuser.hcat.hosts']})

        hive_ms_servers = cluster_spec.determine_component_hosts(
            'HIVE_METASTORE')
        if hive_ms_servers:
            self._replace_config_token(
                cluster_spec, '%HIVE_METASTORE_HOST%',
                hive_ms_servers.pop().fqdn,
                {'webhcat-site': ['templeton.hive.properties']})

        zk_servers = cluster_spec.determine_component_hosts('ZOOKEEPER_SERVER')
        if zk_servers:
            self._replace_config_token(
                cluster_spec, '%ZOOKEEPER_HOST%', zk_servers.pop().fqdn,
                {'webhcat-site': ['templeton.zookeeper.hosts']})

    def finalize_ng_components(self, cluster_spec):
        webhcat_ng = cluster_spec.get_node_groups_containing_component(
            'WEBHCAT_SERVER')[0]
        components = webhcat_ng.components
        if 'HDFS_CLIENT' not in components:
            components.append('HDFS_CLIENT')
        if 'MAPREDUCE_CLIENT' not in components:
            components.append('MAPREDUCE_CLIENT')
        if 'ZOOKEEPER_CLIENT' not in components:
            # if zk server isn't in cluster, add to ng
            if not cluster_spec.get_deployed_node_group_count(
                    'ZOOKEEPER_SERVER'):

                zk_service = next(service for service in cluster_spec.services
                                  if service.name == 'ZOOKEEPER')
                zk_service.deployed = True
                components.append('ZOOKEEPER_SERVER')
            components.append('ZOOKEEPER_CLIENT')

    def pre_service_start(self, cluster_spec, ambari_info, started_services):
        # this code is needed because of a bug in Ambari where hdfs dir's
        # are only created at NN initial startup.  Remove this code when
        # the bug is fixed in Ambari.

        if 'HDFS' not in started_services:
            return

        # get any instance
        with cluster_spec.servers[0].remote as r:
            r.execute_command('su -c "hadoop fs -mkdir /user/hcat" '
                              '-s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chown -R hcat:hdfs '
                              '/user/hcat" -s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chmod -R 755 /user/hcat" '
                              '-s /bin/sh hdfs')

            r.execute_command('su -c "hadoop fs -mkdir /apps/webhcat" '
                              '-s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chown -R hcat:hdfs '
                              '/apps/webhcat" -s /bin/sh hdfs')
            r.execute_command('su -c "hadoop fs -chmod -R 755 /apps/webhcat" '
                              '-s /bin/sh hdfs')


class ZookeeperService(Service):
    def __init__(self):
        super(ZookeeperService, self).__init__(
            ZookeeperService.get_service_id())

    @classmethod
    def get_service_id(cls):
        return 'ZOOKEEPER'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('ZOOKEEPER_SERVER')
        if count != 1:
            raise ex.InvalidComponentCountException(
                'ZOOKEEPER_SERVER', 1, count)


class OozieService(Service):
    def __init__(self):
        super(OozieService, self).__init__(OozieService.get_service_id())
        self.configurations.add('oozie-site')

    @classmethod
    def get_service_id(cls):
        return 'OOZIE'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('OOZIE_SERVER')
        if count != 1:
            raise ex.InvalidComponentCountException(
                'OOZIE_SERVER', 1, count)
        count = cluster_spec.get_deployed_node_group_count('OOZIE_CLIENT')
        if not count:
            raise ex.InvalidComponentCountException(
                'OOZIE_CLIENT', '1+', count)

    def finalize_configuration(self, cluster_spec):
        oozie_servers = cluster_spec.determine_component_hosts('OOZIE_SERVER')
        if oozie_servers:
            self._replace_config_token(
                cluster_spec, '%OOZIE_HOST%', oozie_servers.pop().fqdn,
                {'global': ['oozie_hostname'],
                    'core-site': ['hadoop.proxyuser.oozie.hosts'],
                    'oozie-site': ['oozie.base.url']})

    def finalize_ng_components(self, cluster_spec):
        oozie_ng = cluster_spec.get_node_groups_containing_component(
            'OOZIE_SERVER')[0]
        components = oozie_ng.components
        if 'HDFS_CLIENT' not in components:
            components.append('HDFS_CLIENT')
        if 'MAPREDUCE_CLIENT' not in components:
            components.append('MAPREDUCE_CLIENT')
        # ensure that mr and hdfs clients are colocated with oozie client
        client_ngs = cluster_spec.get_node_groups_containing_component(
            'OOZIE_CLIENT')
        for ng in client_ngs:
            components = ng.components
            if 'HDFS_CLIENT' not in components:
                components.append('HDFS_CLIENT')
        if 'MAPREDUCE_CLIENT' not in components:
            components.append('MAPREDUCE_CLIENT')

    def register_service_urls(self, cluster_spec, url_info):
        oozie_ip = cluster_spec.determine_component_hosts(
            'OOZIE_SERVER').pop().management_ip
        #TODO(jspeidel): Don't hard code port
        url_info['JobFlow'] = {
            'Oozie': 'http://%s:11000' % oozie_ip
        }
        return url_info

    def register_user_input_handlers(self, ui_handlers):
        ui_handlers['oozie-site/oozie.service.JPAService.jdbc.username'] = \
            self._handle_user_property_db_user
        ui_handlers['oozie.service.JPAService.jdbc.password'] = \
            self._handle_user_property_db_pwd

    def _handle_user_property_db_user(self, user_input, configurations):
        oozie_site_config_map = configurations['oozie-site']
        oozie_site_config_map['oozie.service.JPAService.jdbc.username'] = \
            user_input.value
        global_config_map = configurations['global']
        global_config_map['oozie_metastore_user_name'] = user_input.value

    def _handle_user_property_db_pwd(self, user_input, configurations):
        oozie_site_config_map = configurations['oozie-site']
        oozie_site_config_map['oozie.service.JPAService.jdbc.password'] = \
            user_input.value
        global_config_map = configurations['global']
        global_config_map['oozie_metastore_user_passwd'] = user_input.value


class GangliaService(Service):
    def __init__(self):
        super(GangliaService, self).__init__(GangliaService.get_service_id())

    @classmethod
    def get_service_id(cls):
        return 'GANGLIA'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('GANGLIA_SERVER')
        if count != 1:
            raise ex.InvalidComponentCountException('GANGLIA_SERVER', 1, count)

    def is_user_template_component(self, component):
        return component.name != 'GANGLIA_MONITOR'

    def finalize_ng_components(self, cluster_spec):
        for ng in cluster_spec.node_groups.values():
            if 'GANGLIA_MONITOR' not in ng.components:
                ng.components.append('GANGLIA_MONITOR')


class AmbariService(Service):
    def __init__(self):
        super(AmbariService, self).__init__(AmbariService.get_service_id())
        self.configurations.add('ambari')
        #TODO(jspeidel): don't hard code default admin user
        self.admin_user_name = 'admin'

    @classmethod
    def get_service_id(cls):
        return 'AMBARI'

    def validate(self, cluster_spec, cluster):
        count = cluster_spec.get_deployed_node_group_count('AMBARI_SERVER')
        if count != 1:
            raise ex.InvalidComponentCountException('AMBARI_SERVER', 1, count)

    def register_service_urls(self, cluster_spec, url_info):
        ambari_ip = cluster_spec.determine_component_hosts(
            'AMBARI_SERVER').pop().management_ip

        port = cluster_spec.configurations['ambari'].get(
            'server.port', '8080')

        url_info['Ambari Console'] = {
            'Web UI': 'http://{0}:{1}'.format(ambari_ip, port)
        }
        return url_info

    def is_user_template_component(self, component):
        return component.name != 'AMBARI_AGENT'

    def register_user_input_handlers(self, ui_handlers):
        ui_handlers['ambari-stack/ambari.admin.user'] =\
            self._handle_user_property_admin_user
        ui_handlers['ambari-stack/ambari.admin.password'] =\
            self._handle_user_property_admin_password

    def is_mandatory(self):
        return True

    def _handle_user_property_admin_user(self, user_input, configurations):
        admin_user = next(user for user in self.users
                          if user.name == 'admin')
        admin_user.name = user_input.value
        self.admin_user_name = user_input.value

    def _handle_user_property_admin_password(self, user_input, configurations):
        admin_user = next(user for user in self.users
                          if user.name == self.admin_user_name)
        admin_user.password = user_input.value


class SqoopService(Service):
    def __init__(self):
        super(SqoopService, self).__init__(SqoopService.get_service_id())

    @classmethod
    def get_service_id(cls):
        return 'SQOOP'

    def finalize_ng_components(self, cluster_spec):
        sqoop_ngs = cluster_spec.get_node_groups_containing_component('SQOOP')
        for ng in sqoop_ngs:
            if 'HDFS_CLIENT' not in ng.components:
                ng.components.append('HDFS_CLIENT')
            if 'MAPREDUCE_CLIENT' not in ng.components:
                ng.components.append('MAPREDUCE_CLIENT')
