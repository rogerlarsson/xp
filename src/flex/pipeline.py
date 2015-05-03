"""
This module contains the core functionality for modeling and parsing
a pipeline.
"""
import re
import os, os.path
import subprocess
import logging

import blocks

logger = logging.getLogger(os.path.basename(__file__))

# Constants
PIPELINE_PREFIX_VARNAME = 'FLEX_PLN_PREFIX'

#################
# the factory function 
_pipelines = {}
_under_construction = set()

def get_pipeline(filename):
	# resolve filename to canonical absolute path
	filename = os.path.realpath(os.path.abspath(filename))

	# this function is not thread-safe.  So if there's a call for a pipeline
	# that is currently under construction, it's an indication of a circular
	# reference in the dependencies
	if filename in _under_construction:
		raise Exception, 'circular pipeline reference to %s' % filename

	if filename not in _pipelines:
		_under_construction.add(filename)
		pipeline = parse_pipeline(filename)
		_under_construction.remove(filename)
		_pipelines[filename] = pipeline

	return _pipelines[filename]

########################
# Classes used to represent a pipeline

class Pipeline:
	def __init__(self,abs_filename,preamble_stmts,tasks):
		self.name = os.path.basename(abs_filename)
		self.abs_filename = abs_filename
		self.preamble = preamble_stmts
		self.tasks = tasks
		self.used_pipelines = None

		for t in self.tasks:
			t.set_pipeline(self)

		self.initialize()

	def abs_path(self):
		return os.path.dirname(self.abs_filename)

	def initialize(self):
		
		# go through and deal with the extend and use statements
		self.used_pipelines = {}

		i = 0
		stmts = self.preamble
		new_preamble = []
		for stmt in self.preamble:

			if isinstance(stmt,ExtendStatement):
				extended_pipeline = get_pipeline(stmt.filename)

				# insert the preamble
				new_preamble.extend(extended_pipeline.preamble)

				# insert the tasks before the tasks defined here
				existing_tasks = self.tasks
				self.tasks = map(lambda x: x.copy(), extended_pipeline.tasks)
				for t in self.tasks:
					t.set_pipeline(self)
				self.tasks += existing_tasks

				# update the pipelines we depend on
				for k,v in extended_pipeline.used_pipelines.items():
					if k in self.used_pipelines and v != self.used_pipelines[k]:
						raise Exception, 'conflict in aliases for used pipelines: %s' % k
					else:
						self.used_pipelines[k] = v	

			elif isinstance(stmt,UseStatement):
				filename = os.path.join(os.path.dirname(self.abs_filename),stmt.filename)
				used_pipeline = get_pipeline(filename)
				logger.debug('adding pipeline %s with alias %s' % (used_pipeline.name,stmt.alias))
				self.used_pipelines[stmt.alias] = used_pipeline
			else:
				new_preamble.append(stmt)

		self.preamble = new_preamble

		# now link up the tasks with dependencies
		self.task_lookup = dict(map(lambda x: (x.name,x),self.tasks))

		used_task_pattern = re.compile('^(%s)\.(%s)$' % (VAR_PATTERN,VAR_PATTERN))
		for t in self.tasks:
			# we need to do this in case this task was brought in through an extend statement - in which case
			# we will replace its dependencies
			t.clear_dependencies()
			for d in t.dep_names:
				# find the dependency

				m = used_task_pattern.match(d)
				if m:
					pipeline_name = m.group(1)
					task_name = m.group(2)

					if pipeline_name not in self.used_pipelines:
						raise Exception, 'undefined pipeline alias in %s: %s' % (t.name,pipeline_name)

					dep_task = self.used_pipelines[pipeline_name].get_task(task_name)

					if dep_task is None:
						raise Exception, 'dependency of %s not found: %s.%s' % (t.name,pipeline_name,task_name)
					
					t.add_dependency(dep_task)
				else:
					if d in self.task_lookup:
						t.add_dependency(self.task_lookup[d])
					else:
						raise Exception, 'dependency of %s not found: %s' % (t.name,d)

		# Done!

	def build_context(self):
		# build context by processing all variables
		self.context = {}

		# insert the pipline prefix for us to use and for code blocks to use
		self.context[PIPELINE_PREFIX_VARNAME] = '%s_' % self.abs_filename

		for s in self.preamble:
			s.update_context(self.context)

	def get_task(self,task_name):
		if task_name in self.task_lookup:
			return self.task_lookup[task_name]
		else:
			return None

	def get_context(self):
		"""
		Return a dictionary of variables
		"""
		return dict(self.context)

	def unmark_all_tasks(self,recur=False):
		for t in self.tasks:
			t.unmark()

		if recur:
			for pipeline in self.used_pipelines.values():
				pipeline.unmark_all_tasks(recur=True)

	def run(self):
		self.build_context()	

		# TODO: We could optimize this by picking the right
		# tasks first - some sort of graphical sort
		for t in self.tasks:
			logger.debug('running task %s' % t.name)
			t.run()
	
class VariableAssignment:
	def __init__(self,varname,value):
		self.varname = varname
		self.value = value

	def update_context(self,context):
		# TODO: Keep track of the line number
		value = expand_variables(self.value,context,-1)
		logger.debug('expanded "%s" to "%s"' % (self.value,value))

		context[self.varname] = value	

class DeleteVariable:
	def __init__(self,varname):
		self.varname = varname

	def update_context(self,context):
		if self.varname in context:
			del context[self.varname]

class ExtendStatement:
	def __init__(self,filename):
		self.filename = filename
		
class UseStatement: 
	def __init__(self,filename,alias=None): 
		self.filename = filename 
		if alias is not None: 
			self.alias = alias 
		else: 
			self.alias = filename

class Task:
	def __init__(self,name,dep_names,blocks):
		self.name = name
		self.dep_names = dep_names
		self.blocks = blocks

		self._dependencies = []

	def copy(self):
		blocks = map(lambda x: x.copy(), self.blocks)
		return Task(self.name,self.dep_names,blocks)

	def set_pipeline(self,pipeline):
		self.pipeline = pipeline

	def clear_dependencies(self):
		self._dependencies = []

	def add_dependency(self,task):
		self._dependencies.append(task)

	def mark_file(self):
		return os.path.join(self.pipeline.abs_path(),'.%s-%s.mark' % (self.pipeline.name,self.name))

	def unmark(self):
		# Remove the marker file
		if self.is_marked():
			os.remove(self.mark_file())
	
	def mark(self):
		mark_file = self.mark_file()

		logger.debug('writing mark file: %s' % mark_file)
		# Make the marker file
		fh = open(mark_file,'w')
		fh.close()

	def is_marked(self):
		return os.path.exists(self.mark_file())

	def run(self,force=False,skip_dependencies=False):

		logger.debug('task %s: marked = %d' % (self.name,self.is_marked()))
		if not force and self.is_marked():
			return

		# first run all dependencies
		if not skip_dependencies:
			logger.debug('task %s: run dependencies' % self.name)
			for d in self._dependencies:
				d.run(skip_dependencies)
		else:
			logger.debug('task %s: skipping dependencies' % self.name)
		
		context = self.pipeline.get_context()
		cwd = self.pipeline.abs_path()

		# run this tasks
		logger.debug('task %s: running blocks...' % self.name)
		for b in self.blocks:
			logger.debug('task %s: running block %s' % (self.name,str(b.__class__)))
			b.run(context,cwd)

		# Update the marker
		self.mark()

class ExportBlock:
	def __init__(self,statements):
		self.statements = statements

	def copy(self):
		return ExportBlock(self.statements)

	def run(self,context,cwd):
		# modify the context - that's all the export block can do
		self.update_context(context)

	def update_context(self,context):
		for s in self.statements:
			s.update_context(context)

class CodeBlock:
	def __init__(self,lang,content):
		self.lang = lang
		self.content = content

	def copy(self):
		return CodeBlock(self.lang,self.content)

	def run(self,context,cwd):
		# expand any variables in the content
		# TODO: Update line numbers
		content = []
		for line in self.content:
			content.append(expand_variables(line,context,-1))

		logger.debug('expanded\n%s\n\nto\n%s' % (self.content,content))

		if self.lang == 'sh':
			try:
				blocks.run_shell(context,cwd,content)
			except subprocess.CalledProcessError:
				raise BlockFailed

		elif self.lang == 'py':
			blocks.run_python(context,cwd,content)

class BlockFailed(Exception):
	pass

class ParseException(Exception):
	
	def __init__(self,lineno,message):
		Exception.__init__(self,message)
		self.lineno = lineno

class VariableValueException(Exception):
	
	def __init__(self,lineno,message):
		Exception.__init__(self,message)
		self.lineno = lineno

VAR_PATTERN = '\w[\w\d_]*'
FILE_PATTERN = '.+?'

def parse_pipeline(pipeline_file):
	
	# read in the content of the file
	pipeline_file = os.path.realpath(os.path.abspath(pipeline_file))
	lines = open(pipeline_file,'r').readlines()

	# read the preamble
	extend_pattern = re.compile('^extend\s+(%s)$' % FILE_PATTERN)
	use_pattern = re.compile('^use\s+(%s)(\s+as\s+(%s))?$' % (FILE_PATTERN,VAR_PATTERN))
	var_assign_pattern = re.compile('^(%s)\s*=(.+)' % VAR_PATTERN)
	var_del_pattern = re.compile('^unset\s+(%s)$' % VAR_PATTERN)

	lineno = 0
	in_preamble = True
	statements = []
	while lineno < len(lines) and in_preamble:
		cur_line = lines[lineno].strip()

		m = extend_pattern.match(cur_line)
		if m:	
			# load in the pipeline
			fname = m.group(1)
			complete_fname = os.path.join(os.path.dirname(pipeline_file),fname)
			statements.append(ExtendStatement(complete_fname))
		else:
			m = var_assign_pattern.match(cur_line)
			if m:
				logger.debug('found variable assignment: %s,%s' % (m.group(1),m.group(2)))
				statements.append(VariableAssignment(m.group(1),m.group(2)))
			else:
				m = var_del_pattern.match(cur_line)
				if m:
					logger.debug('found delete variable: %s' % m.group(1))
					statements.append(DeleteVariable(m.group(1)))
				else:
					m = use_pattern.match(cur_line)
					if m:
						alias = m.group(3)
						logger.debug('found use: %s,%s' % (m.group(1),str(m.group(3))))
						statements.append(UseStatement(m.group(1),alias))
					elif len(cur_line) == 0 or cur_line.startswith('#'):
						pass
					else:
						in_preamble = False

		if in_preamble:
			lineno += 1
			
	# read the tasks
	task_pattern = re.compile('^(%s)\s*:(.*)$' % VAR_PATTERN)
	tasks = []

	while lineno < len(lines):
		cur_line = lines[lineno].rstrip()

		if len(cur_line) == 0 or cur_line.strip().startswith('#'):
			lineno += 1
		else:
			m = task_pattern.match(cur_line)
			if m:
				task_name = m.group(1)
				dep_str = m.group(2)

				logger.debug('parsing task: %s' % task_name)

				lineno,task = parse_task(task_name,dep_str,lines,lineno)

				tasks.append(task)
			else:
				raise ParseException(lineno,'expected a task definition, got: %s' % cur_line)

	# make the pipeline
	pipeline = Pipeline(pipeline_file,statements,tasks)

	# return the pipeline
	return pipeline

def parse_task(task_name,dep_str,lines,lineno):
	"""
	Parse the task.

	lineno is the line number where the task definition starts - on which the
	task_name and dependencies string appears.

	Raise exception if the task was invalid otherwise, 
	return (next_lineno,Task)
	"""
	valid_dep_string = re.compile('^%s(\.%s)?$' % (VAR_PATTERN,VAR_PATTERN))
	var_assignment_pattern = re.compile('^(%s)\s*=(.*)$' % VAR_PATTERN)
	delete_var_pattern = re.compile('^unset\s+(%s)$' % VAR_PATTERN)
	code_pattern = re.compile('^\tcode\.(\w+):$')
	export_pattern = re.compile('^\texport:$')

	# parse the dependency list
	dependencies = dep_str.split()
	
	for dep in dependencies:
		if valid_dep_string.match(dep) is None:
			raise ParseException(lineno,'expected a dependency, got: %s' % dep)

	lineno += 1
	# now parse blocks
	blocks = []
	in_task = True	

	while lineno < len(lines) and in_task:
		cur_line = lines[lineno].rstrip()

		mc = code_pattern.match(cur_line)
		me = export_pattern.match(cur_line)

		if mc:
			lang = mc.group(1)
			logger.debug('found code block at line %d' % lineno)
			lineno,content = read_block_content(lines,lineno+1)

			blocks.append(CodeBlock(lang,content))
		elif me:
			logger.debug('found export block at line %d' % lineno)

			new_lineno,content = read_block_content(lines,lineno+1)
			
			# parse the content as variable assignments
			statements = []
			for i,line in enumerate(content):
				line = line.rstrip()
				ma = var_assignment_pattern.match(line)
				md = delete_var_pattern.match(line)
				if ma:
					statements.append(VariableAssignment(ma.group(1),ma.group(2)))
				elif md:
					statements.append(DeleteVariable(ma.group(1)))
				elif len(line) == 0:
					pass
				else:
					raise ParseException(lineno+1+i,'expected a variable assignment, got: %s' % line)

			blocks.append(ExportBlock(statements))
			lineno = new_lineno
		else:
			in_task = False	

	return lineno,Task(task_name,dependencies,blocks)

def read_block_content(lines,lineno):
	"""
	Extract block content - begins with two tabs.

	Return: (next_lineno,content)
	"""
	last_lineno = lineno
	content_lines = []
	while last_lineno < len(lines):
		if lines[last_lineno].startswith('\t\t'):
			content_lines.append(lines[last_lineno])
		elif len(lines[last_lineno].strip()) == 0 or lines[last_lineno].strip().startswith('#'):
			pass
		else:
			break
		last_lineno += 1
	
	return last_lineno,map(lambda x: x[2:].rstrip(),lines[lineno:last_lineno])

variable_pattern = re.compile('([\w\d_]+|\{[\w\d_]+?\})')
SUPPORTED_BUILTIN_FUNCTIONS = ['','PLN']

def expand_variables(x,context,lineno,nested=False):
	"""
	This function will both parse variables in the 
	string (assumed to be one line of text) and replace them
	with their appropriate values given this context.
	"""
	
	cpos = 0
	
	while cpos < len(x):
		
		# handle escaping special character
		if x[cpos] == '\\':
			if cpos == len(x)-1:
				raise ParseException(lineno,'incomplete escape sequence')

			c = x[cpos+1]
			replacement = c
			pre_escape = x[:cpos]
			post_escape = x[(cpos+2):]
			x = pre_escape + replacement + post_escape
			cpos = cpos+2
			
		elif x[cpos] == '$':
			# variable started!
			if cpos == len(x)-1:
				raise ParseException(lineno,'incomplete variable reference')

			# get the variable name
			m = variable_pattern.match(x[(cpos+1):])
			if m is None:
				# check if this is a shell call
				if cpos < len(x)-1 and x[cpos+1] == '(':
					varname = ''
				else:
					raise ParseException(lineno,'invalid variable reference')
			else:
				varname = m.group(1)
	
			# remove curly braces if needed
			if varname.startswith('{'):
				varname = varname[1:-1]
				
				# remove the curlies from the string x
				x = x[:(cpos+1)] + varname + x[(cpos+len(varname)+3):]

			# if this variable reference is actually a function
			fxn_paren_pos = cpos+1+len(varname)
			if fxn_paren_pos < (len(x)-1) and x[fxn_paren_pos] == '(':
				fxn_argstart_pos = fxn_paren_pos + 1
				# we only support two functions
				if varname not in SUPPORTED_BUILTIN_FUNCTIONS:
					raise ParseException(lineno,'invalid builtin function name: %s' % varname)

				# process the rest of the string
				expanded_x_part,eofxn = expand_variables(x[fxn_argstart_pos:],context,lineno,nested=True)

				x = x[:fxn_argstart_pos] + expanded_x_part
				eofxn = fxn_argstart_pos + eofxn

				# TODO: Should we add support for multiple arguments?
				args = x[fxn_argstart_pos:eofxn]

				# apply the function
				ret_val = ''
				if varname == '':
					raise ParseException(lineno,'shell evaluations are not currently supported')
				elif varname == 'PLN':
					# TODO: Add a switch for supporting storage to pipeline-specific directory
					ret_val = '%s%s' % (context[PIPELINE_PREFIX_VARNAME],args)

				# make the replacement
				pre_fxn = x[:cpos]
				post_fxn = x[(eofxn+1):]
				x = pre_fxn + ret_val + post_fxn
				cpos = len(pre_fxn) + len(ret_val)

			else:
				replacement = ''

				if varname not in context:
					raise VariableValueException(lineno,'variable %s does not exist' % varname)
	
				replacement = context[varname]	
	
				# make the replacement
				pre_var = x[:cpos]
				post_var = x[(cpos+1+len(varname)):]
				x = pre_var + replacement + post_var
				cpos = cpos+1+len(replacement)
		
		elif nested and x[cpos] == ')':
			# We just found the end of a function (which we're nested inside of)
			return x,cpos

		else:
			cpos += 1

	# under normal circumstances, reaching the end of the string is what we want.
	# but if we are nested, then we should find a parenthesis first.
	if nested:
		raise ParseException(lineno,'expected to find a ")", none found')

	return x