import unittest
from flex.pipeline import *
import flex.pipeline as pipeline
import os, os.path
import time

BASE_PATH = os.path.dirname(__file__)

def get_complete_filename(fname):
	return os.path.join(BASE_PATH,'pipelines',fname)

class AbstractionTestCase(unittest.TestCase):
	
	def test_run_abstract_pipeline(self):
		p = get_pipeline(get_complete_filename('abs_pipeline.fx'),default_prefix=USE_FILE_PREFIX)
		p.unmark_all_tasks(recur=True)
		try:
			p.run()
			self.fail()
		except Exception, e:
			print e

	def test_run_non_abstract_pipeline(self):
		p = get_pipeline(get_complete_filename('nonabs_pipeline.fx'),default_prefix=USE_FILE_PREFIX)
		p.unmark_all_tasks(recur=True)
		p.run()

class ConfigValueSettingTestCase(unittest.TestCase):
	
	def test_top_level_configs(self):
		p = get_pipeline(get_complete_filename('tlc_pipeline.fx'),default_prefix=USE_FILE_PREFIX)

		self.assertEquals(p.config['foobar'],'barfoo')

	def test_nested_configs(self):
		p = get_pipeline(get_complete_filename('nlc_pipeline.fx'),default_prefix=USE_FILE_PREFIX)

		self.assertEquals(p.config['foo']['bar']['foo'],'barfoo')
