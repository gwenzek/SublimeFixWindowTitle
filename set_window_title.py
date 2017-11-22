import sublime

import os
import time
from sublime_plugin import EventListener

WAS_DIRTY = "set_window_title_was_dirty"
PLATFORM = sublime.platform()

if PLATFORM == 'windows':
  
  class Window:
    import ctypes as c
    u = c.windll.user32
    init_user32_done = False
    HWND = c.c_void_p
    LPARAM = c.c_void_p
    WNDENUMPROC = c.WINFUNCTYPE(c.c_bool, HWND, LPARAM)
    LPCWSTR = c.POINTER(c.c_uint16)

    def __init__(self, handle):
      self.handle = handle
      self.init_user32()

    def __str__(self):
      return "Window"

    def __repr__(self):
      return "Window({})".format(self.handle)

    @property
    def title(self):
      # Windows encodes window titles as an array of uint16s under UTF-16
      # To convert this to python, we cast this to an array of bytes, then decode it
      n = self.u.GetWindowTextLengthW(self.handle)
      t = (self.c.c_uint16 * (n+1))()
      n2 = self.u.GetWindowTextW(self.handle, t, n+1)
      assert n == n2
      return self.c.POINTER(self.c.c_char)(t)[0:n2*2].decode('utf16')

    @title.setter
    def title(self, title):
      b = title.encode('utf16')
      n = len(b)
      t = (self.c.c_char * (n+10))(*b)
      self.u.SetWindowTextW(self.handle, self.LPCWSTR(t))

    @classmethod
    def all(cls):
      handles = []
      def cb(handle, x):
        handles.append(handle)
        return True
      cls.u.EnumWindows(cls.WNDENUMPROC(cb), None)
      return [cls(handle) for handle in handles]

    @classmethod
    def init_user32(cls):
      if not cls.init_user32_done:
        cls.u.EnumWindows.argtypes = [cls.WNDENUMPROC, cls.LPARAM]
        cls.u.EnumWindows.restype = cls.c.c_bool
        cls.u.GetWindowTextLengthW.argtypes = [cls.HWND]
        cls.u.GetWindowTextLengthW.restype = cls.c.c_int
        cls.u.GetWindowTextW.argtypes = [cls.HWND, cls.LPCWSTR, cls.c.c_int]
        cls.u.GetWindowTextW.restype = cls.c.c_int
        cls.u.SetWindowTextW.argtypes = [cls.HWND, cls.LPCWSTR]
        cls.u.SetWindowTextW.restype = cls.c.c_bool
        cls.init_user32_done = True

class SetWindowTitle(EventListener):

  script_path = None
  ready = False

  def __init__(self):
    sublime.set_timeout_async(self.on_sublime_started, 1000)
    self.window_handle_cache = dict()

  def on_sublime_started(self):
    packages_path = sublime.packages_path()
    while not packages_path:
      packages_path = sublime.packages_path()
      time.sleep(1)

    if PLATFORM == 'linux':
      self.script_path = os.path.join(packages_path, __package__,
                                    "fix_window_title.sh")
    self.ready = True

    for window in sublime.windows():
      self.run(window.active_view())

  def on_activated_async(self, view):
    self.run(view)

  def on_modified_async(self, view):
    if view.settings().get(WAS_DIRTY, None) != view.is_dirty():
      self.run(view)

  def on_post_save_async(self, view):
    self.run(view)

  def run(self, view):
    if not self.ready:
      print("[SetWindowTitle] Info: ST haven't finished loading yet, skipping.")
      return

    project = self.get_project(view)

    official_title = self.get_official_title(view, project)
    new_title = self.get_new_title(view, project, official_title)
    self.rename_window(view.window(), official_title, new_title)
    view.settings().set(WAS_DIRTY, view.is_dirty())

  def get_project(self, view):
    project = None
    window = view.window()
    if not window:
      return

    project = window.project_file_name()
    if not project:
      folders = window.folders()
      project = folders[0] if folders else ""
    if project:
      project = os.path.basename(project)
      project = os.path.splitext(project)[0]

    return project

  def get_official_title(self, view, project):
    """Returns the official name for a given view.

    Note: The full file path isn't computed,
    because ST uses `~` to shorten the path.
    """
    view_name = view.name() or view.file_name() or "untitled"
    official_title = os.path.basename(view_name)
    if view.is_dirty():
      official_title += " •"
    if project:
      official_title += " (%s)" % project
    official_title += " - Sublime Text"
    return official_title

  def get_new_title(self, view, project, old_title):
    """Returns the new name for a view, according to the user preferences."""
    settings = sublime.load_settings("set_window_title.sublime-settings")

    path = self._get_displayed_path(view, settings)

    if view.file_name():
      full_path = view.file_name()

    template = settings.get("template")
    template = self._replace_condition(template, "has_project", project,
                                       settings)
    template = self._replace_condition(template, "is_dirty",
                                       view.is_dirty(), settings)

    return template.format(path=path, project=project)

  def _get_displayed_path(self, view, settings):
    view_name = view.name()
    # view.name() is set by other plugins so it's probably the best choice.
    if view_name:
      return view_name

    full_path = view.file_name()
    if not full_path:
      return settings.get("untitled", "untitled")

    display = settings.get("path_display")
    if display in ("full", "shortest"):
      home = os.environ.get("HOME")
      if home and full_path.startswith(home):
        full_path = "~" + full_path[len(home):]

    if display in ("relative", "shortest"):
      window = view.window()
      folders = window.folders() if window else None
      root = folders[0] if folders else None
      rel_path = os.path.relpath(full_path, root) if root and os.path.splitdrive(full_path)[0] == os.path.splitdrive(root)[0] else full_path

    if display == "full":
      return full_path
    elif display == "relative":
      return rel_path
    else:  # default to "shortest"
      return full_path if len(full_path) <= len(rel_path) else rel_path

  def _replace_condition(self, template, condition, value, settings):
    if value:
      replacement = settings.get(condition + "_true")
    else:
      replacement = settings.get(condition + "_false")
    return template.replace("{%s}" % condition, replacement)

  def rename_window(self, window, official_title, new_title):
    """Rename a subl window using the fix_window_title.sh script."""
    settings = sublime.load_settings("set_window_title.sublime-settings")
    debug = settings.get("debug")
    if PLATFORM == 'linux':
      cmd = 'bash %s "%s" "%s"' % (self.script_path, official_title, new_title)
      if debug:
        print("[SetWindowTitle] Debug: running: ", cmd)
      output = os.popen(cmd + " 1&2").read()
      if debug:
        print("[SetWindowTitle] Debug: result: ", output)
    elif PLATFORM == 'windows':
      w = self.window_handle_cache.get(window.id(), None)
      if w is None:
        for w in Window.all():
          if official_title in w.title:
            w.title = new_title
            # self.window_handle_cache[window.id()] = w
      else:
        w.title = new_title
