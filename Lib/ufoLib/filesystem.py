import os
import shutil
from io import StringIO, BytesIO, open
import zipfile

haveFS = False
try:
	import fs
	from fs.osfs import OSFS
	from fs.zipfs import ZipFS, ZipOpenError
	haveFS = True
except ImportError:
	pass

from ufoLib.plistlib import readPlist, writePlist
from ufoLib.errors import UFOLibError

try:
	basestring
except NameError:
	basestring = str

def sniffFileStructure(path):
	if zipfile.is_zipfile(path):
		return "zip"
	elif os.path.isdir(path):
		return "package"
	raise UFOLibError("The specified UFO does not have a known structure.")


class FileSystem(object):

	def __init__(self, path, mode="r", structure=None):
		"""
		path can be a path or a fs file system object.

		mode can be r or w.

		structure is only applicable in "w" mode. Options:
		None: existing structure
		package: package structure
		zip: zipped package

		mode and structure are both ignored if a
		fs file system object is given for path.
		"""
		self._root = None
		self._path = "<data stream>"
		if isinstance(path, basestring):
			self._path = path
			if mode == "w":
				if os.path.exists(path):
					existingStructure = sniffFileStructure(path)
					if structure is not None:
						if structure != existingStructure:
							raise UFOLibError("A UFO with a different structure already exists at the given path.")
					else:
						structure = existingStructure
			elif mode == "r":
				if not os.path.exists(path):
					raise UFOLibError("The specified UFO doesn't exist.")
				structure = sniffFileStructure(path)
			if structure == "package":
				if mode == "w" and not os.path.exists(path):
					os.mkdir(path)
				path = OSFS(path)
			elif structure == "zip":
				if not haveFS:
					raise UFOLibError("The fs module is required for reading and writing UFO ZIP.")
				path = ZipFS(path, mode=mode, allow_zip_64=True, encoding="utf8")
				roots = path.listdir("")
				if not roots:
					self._root = "contents"
				elif len(roots) > 1:
					raise UFOLibError("The UFO contains more than one root.")
				else:
					self._root = roots[0]
		self._fs = path

	def close(self):
		self._fsClose()

	# --------
	# fs Calls
	# --------

	"""
	All actual low-level file system interaction
	MUST be done through these methods.

	This is necessary because ZIPs will have a
	top level root directory that packages will
	not have.
	"""

	def _fsRootPath(self, path):
		if self._root is None:
			return path
		return self.joinPath(self._root, path)

	def _fsUnRootPath(self, path):
		if self._root is None:
			return path
		return self.relativePath(path, self._root)

	def _fsClose(self):
		self._fs.close()

	def _fsOpen(self, path, mode="r", encoding=None):
		path = self._fsRootPath(path)
		f = self._fs.open(path, mode, encoding=encoding)
		return f

	def _fsRemove(self, path):
		path = self._fsRootPath(path)
		self._fs.remove(path)

	def _fsMakeDirectory(self, path):
		path = self._fsRootPath(path)
		self._fs.makedir(path)

	def _fsRemoveDirectory(self, path):
		path = self._fsRootPath(path)
		self._fs.removedir(path, force=True)

	def _fsMove(self, path1, path2):
		if self.isDirectory(path1):
			meth = self._fs.movedir
		else:
			meth = self._fs.move
		path1 = self._fsRootPath(path1)
		path2 = self._fsRootPath(path2)
		meth(path1, path2)

	def _fsExists(self, path):
		path = self._fsRootPath(path)
		return self._fs.exists(path)

	def _fsIsDirectory(self, path):
		path = self._fsRootPath(path)
		return self._fs.isdir(path)

	def _fsListDirectory(self, path):
		path = self._fsRootPath(path)
		return self._fs.listdir(path)

	def _fsGetFileModificationTime(self, path):
		path = self._fsRootPath(path)
		info = self._fs.getinfo(path)
		return info["modified_time"]

	# -----------------
	# Path Manipulation
	# -----------------

	def joinPath(self, *parts):
		return fs.path.join(*parts)

	def splitPath(self, path):
		return fs.path.split(path)

	def directoryName(self, path):
		return fs.path.dirname(path)

	def relativePath(self, path, start):
		return fs.relativefrom(path, start)

	# ---------
	# Existence
	# ---------

	def exists(self, path):
		return self._fsExists(path)

	def isDirectory(self, path):
		return self._fsIsDirectory(path)

	def listDirectory(self, path, recurse=False):
		return self._listDirectory(path, recurse=recurse, relativeTo=path)

	def _listDirectory(self, path, recurse=False, relativeTo=None, depth=0, maxDepth=100):
		if not relativeTo.endswith("/"):
			relativeTo += "/"
		if depth > maxDepth:
			raise UFOLibError("Maximum recusion depth reached.")
		result = []
		for fileName in self._fsListDirectory(path):
			p = self.joinPath(path, fileName)
			if self.isDirectory(p) and recurse:
				result += self._listDirectory(p, recurse=True, relativeTo=relativeTo, depth=depth+1, maxDepth=maxDepth)
			else:
				p = p[len(relativeTo):]
				result.append(p)
		return result

	def makeDirectory(self, path):
		if not self.exists(path):
			self._fsMakeDirectory(path)

	# -----------
	# File Opener
	# -----------

	def open(self, path, mode="r", encoding=None):
		"""
		Returns a file (or file-like) object for the
		file at the given path. The path must be relative
		to the UFO path. Returns None if the file does
		not exist and the mode is "r" or "rb. An encoding
		may be passed if needed.

		Note: The caller is responsible for closing the open file.
		"""
		if encoding:
			if mode == "r":
				mode = "rb"
			elif mode == "w":
				mode = "wb"
		if mode in ("r", "rb") and not self.exists(path):
			return None
		if self.exists(path) and self.isDirectory(path):
			raise UFOLibError("%s is a directory." % path)
		if mode in ("w", "wb"):
			self._buildDirectoryTree(path)
		f = self._fsOpen(path, mode, encoding=encoding)
		return f

	def _buildDirectoryTree(self, path):
		directory, fileName = self.splitPath(path)
		directoryTree = []
		while directory:
			directory, d = self.splitPath(directory)
			directoryTree.append(d)
		directoryTree.reverse()
		built = ""
		for d in directoryTree:
			d = self.joinPath(built, d)
			self.makeDirectory(d)
			built = d

	# ------------------
	# Modification Times
	# ------------------

	def getFileModificationTime(self, path):
		"""
		Returns the modification time (as reported by os.path.getmtime)
		for the file at the given path. The path must be relative to
		the UFO path. Returns None if the file does not exist.
		"""
		if not self.exists(path):
			return None
		return self._fsGetFileModificationTime(path)

	# --------------
	# Raw Read/Write
	# --------------

	def readBytesFromPath(self, path, encoding=None):
		"""
		Returns the bytes in the file at the given path.
		The path must be relative to the UFO path.
		Returns None if the file does not exist.
		An encoding may be passed if needed.
		"""
		f = self.open(path, mode="rb", encoding=encoding)
		if f is None:
			return None
		data = f.read()
		f.close()
		return data

	def writeBytesToPath(self, path, data, encoding=None):
		"""
		Write bytes to path. If needed, the directory tree
		for the given path will be built. The path must be
		relative to the UFO. An encoding may be passed if needed.
		"""
		if encoding:
			data = StringIO(data).encode(encoding)
		self._writeFileAtomically(data, path)

	def _writeFileAtomically(self, data, path):
		"""
		Write data into a file at path. Do this sort of atomically
		making it harder to cause corrupt files. This also checks to see
		if data matches the data that is already in the file at path.
		If so, the file is not rewritten so that the modification date
		is preserved.
		"""
		assert isinstance(data, bytes)
		if self.exists(path):
			f = self.open(path, "rb")
			oldData = f.read()
			f.close()
			if data == oldData:
				return
		if data:
			f = self.open(path, "wb")
			f.write(data)
			f.close()

	# ------------
	# File Removal
	# ------------

	def remove(self, path):
		"""
		Remove the file (or directory) at path. The path
		must be relative to the UFO.
		"""
		if not self.exists(path):
			return
		self._removeFileForPath(path, raiseErrorIfMissing=True)

	def _removeFileForPath(self, path, raiseErrorIfMissing=False):
		if not self.exists(path):
			if raiseErrorIfMissing:
				raise UFOLibError("The file %s does not exist." % path)
		else:
			if self.isDirectory(path):
				self._fsRemoveDirectory(path)
			else:
				self._fsRemove(path)
		directory = self.directoryName(path)
		if directory:
			self._removeEmptyDirectoriesForPath(directory)

	def _removeEmptyDirectoriesForPath(self, directory):
		if not self.exists(directory):
			return
		if not len(self._fsListDirectory(directory)):
			self._fsRemoveDirectory(directory)
		else:
			return
		directory = self.directoryName(directory)
		if directory:
			self._removeEmptyDirectoriesForPath(directory)

	# ----
	# Move
	# ----

	def move(self, path1, path2):
		if not self.exists(path1):
			raise UFOLibError("%s does not exist." % path1)
		if self.exists(path2):
			raise UFOLibError("%s already exists." % path2)
		self._fsMove(path1, path2)

	# --------------
	# Property Lists
	# --------------

	def readPlist(self, path, default=None):
		"""
		Read a property list relative to the
		UFO path. If the file is missing and
		default is None a UFOLibError will be
		raised. Otherwise default is returned.
		The errors that could be raised during
		the reading of a plist are unpredictable
		and/or too large to list, so, a blind
		try: except: is done. If an exception
		occurs, a UFOLibError will be raised.
		"""
		if not self.exists(path):
			if default is not None:
				return default
			else:
				raise UFOLibError("%s is missing. This file is required" % path)
		try:
			with self.open(path, "rb") as f:
				return readPlist(f)
		except:
			raise UFOLibError("The file %s could not be read." % path)

	def writePlist(self, path, obj):
		"""
		Write a property list.

		Do this sort of atomically, making it harder to
		cause corrupt files, for example when writePlist
		encounters an error halfway during write. This
		also checks to see if text matches the text that
		is already in the file at path. If so, the file
		is not rewritten so that the modification date
		is preserved.

		The errors that could be raised during the writing
		of a plist are unpredictable and/or too large to list,
		so, a blind try: except: is done. If an exception occurs,
		a UFOLibError will be raised.
		"""
		try:
			f = BytesIO()
			writePlist(obj, f)
			data = f.getvalue()
		except:
			raise UFOLibError("The data for the file %s could not be written because it is not properly formatted." % path)
		self.writeBytesToPath(path, data)


class _NOFS(object):

	def __init__(self, path):
		self._path = path

	def _absPath(self, path):
		return os.path.join(self._path, path)

	def joinPath(self, *parts):
		return os.path.join(*parts)

	def splitPath(self, path):
		return os.path.split(path)

	def directoryName(self, path):
		return os.path.split(path)[0]

	def relativePath(self, path, start):
		return os.path.relpath(path, start)

	def close(self):
		pass

	def open(self, path, mode, encoding=None):
		path = self._absPath(path)
		return open(path, mode=mode, encoding=encoding)

	def remove(self, path):
		path = self._absPath(path)
		os.remove(path)

	def makedir(self, path):
		path = self._absPath(path)
		os.mkdir(path)

	def removedir(self, path):
		path = self._absPath(path)
		shutil.rmtree(path)

	def move(self, path1, path2):
		path1 = self._absPath(path1)
		path2 = self._absPath(path2)
		os.move(path1, path2)

	def movedir(self, path1, path2):
		path1 = self._absPath(path1)
		path2 = self._absPath(path2)
		shutil.move(path1, path2)

	def exists(self, path):
		path = self._absPath(path)
		return os.path.exists(path)

	def isdir(self, path):
		path = self._absPath(path)
		return os.path.isdir(path)

	def listdir(self, path):
		path = self._absPath(path)
		return os.listdir(path)

	def getinfo(self, path):
		path = self._absPath(path)
		stat = os.stat(path)
		info = dict(
			modified_time=stat.st_mtime
		)
		return info


if not haveFS:
	OSFS = _NOFS