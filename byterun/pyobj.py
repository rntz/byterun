"""Implementations of Python fundamental objects for Byterun."""

import collections
import inspect
import types

import six

PY3, PY2 = six.PY3, not six.PY3


def make_cell(value):
    # Thanks to Alex Gaynor for help with this bit of twistiness.
    # Construct an actual cell object by creating a closure right here,
    # and grabbing the cell object out of the function we create.
    fn = (lambda x: lambda: x)(value)
    if PY3:
        return fn.__closure__[0]
    else:
        return fn.func_closure[0]


class Function(object):
    __slots__ = [
        'func_code', 'func_name', 'func_defaults', 'func_globals',
        'func_locals', 'func_dict', 'func_closure',
        '__name__', '__dict__', '__doc__',
        '_vm', '_func',
    ]

    def __init__(self, name, code, globs, defaults, closure, vm):
        self._vm = vm
        self.func_code = code
        self.func_name = self.__name__ = name or code.co_name
        self.func_defaults = tuple(defaults)
        self.func_globals = globs
        self.func_locals = self._vm.frame.f_locals
        self.__dict__ = {}
        self.func_closure = closure
        self.__doc__ = code.co_consts[0] if code.co_consts else None

        # Sometimes, we need a real Python function.  This is for that.
        kw = {
            'argdefs': self.func_defaults,
        }
        if closure:
            kw['closure'] = tuple(make_cell(0) for _ in closure)
        self._func = types.FunctionType(code, globs, **kw)

    def __repr__(self):         # pragma: no cover
        return '<Function %s at 0x%08x>' % (
            self.func_name, id(self)
        )

    def __get__(self, instance, owner):
        if instance is not None:
            return Method(instance, owner, self)
        if PY2:
            return Method(None, owner, self)
        else:
            return self

    def __call__(self, *args, **kwargs):
        if PY2 and self.func_name in ["<setcomp>", "<dictcomp>", "<genexpr>"]:
            # D'oh! http://bugs.python.org/issue19611 Py2 doesn't know how to
            # inspect set comprehensions, dict comprehensions, or generator
            # expressions properly.  They are always functions of one argument,
            # so just do the right thing.
            assert len(args) == 1 and not kwargs, "Surprising comprehension!"
            callargs = {".0": args[0]}
        else:
            try:
                callargs = inspect.getcallargs(self._func, *args, **kwargs)
            except Exception as e:
                # import pudb;pudb.set_trace() # -={XX}=-={XX}=-={XX}=-
                raise
        frame = self._vm.make_frame(
            self.func_code, callargs, self.func_globals, {}
        )
        CO_GENERATOR = 32           # flag for "this code uses yield"
        if self.func_code.co_flags & CO_GENERATOR:
            gen = Generator(frame, self._vm)
            frame.generator = gen
            retval = gen
        else:
            retval = self._vm.run_frame(frame)
        return retval

if PY2:
    class Class(object):
        def __init__(self, name, bases, methods):
            # These are double-underscored so that compute_mro doesn't need to
            # distinguish between our Classes and Python's classes.
            self.__name__ = name
            self.__bases__ = bases
            self.locals = dict(methods)
            self.__mro__ = self._compute_mro(self)
            self.enable_setattr = () # value is irrelevant

        def __call__(self, *args, **kw):
            return Object(self, args, kw)

        def __repr__(self):         # pragma: no cover
            return '<Class %s at 0x%08x>' % (self.__name__, id(self))

        def __getattr__(self, name):
            val = self.resolve_attr(name)
            # Check if we have a descriptor
            if hasattr(val, '__get__'):
                return val.__get__(None, self)
            # Not a descriptor, return the value.
            return val

        def __setattr__(self, name, value):
            if not hasattr(self, 'enable_setattr'):
                return object.__setattr__(self, name, value)
            # TODO: what about data descriptors on classes? is that possible?
            self.locals[name] = value

        @classmethod
        def mro_merge(cls, seqs):
            """
            Merge a sequence of MROs into a single resulting MRO.
            This code is copied from the following URL with print statments removed.
            https://www.python.org/download/releases/2.3/mro/
            """
            res = []
            while True:
                nonemptyseqs = [seq for seq in seqs if seq]
                if not nonemptyseqs:
                    return res
                for seq in nonemptyseqs:  # find merge candidates among seq heads
                    cand = seq[0]
                    nothead = [s for s in nonemptyseqs if cand in s[1:]]
                    if nothead:
                        cand = None  # reject candidate
                    else:
                        break
                if not cand:
                    raise TypeError("Illegal inheritance.")
                res.append(cand)
                for seq in nonemptyseqs:  # remove candidate
                    if seq[0] == cand:
                        del seq[0]

        @classmethod
        def _compute_mro(cls, c):
            """
            Compute the class precedence list (mro) according to C3.
            This code is copied from the following URL with print statments removed.
            https://www.python.org/download/releases/2.3/mro/
            """
            return tuple(cls.mro_merge([[c]] +
                                       [list(base.__mro__) for base in c.__bases__]
                                       + [list(c.__bases__)]))

        def resolve_attr(self, name):
            """
            Find an attribute in our MRO and return it raw (i.e. without any
            special-case handling of descriptors such as method wrapping, etc).
            """
            for base in self.__getattribute__('__mro__'):
                if isinstance(base, Class):
                    if name in base.locals:
                        return base.locals[name]
                else:
                    if name in base.__dict__:
                        # Avoid using getattr so we can handle method wrapping
                        return base.__dict__[name]
            raise AttributeError(
                "type object %r has no attribute %r" % (self.__name__, name)
            )

    class Object(object):
        def __init__(self, _class, args, kw):
            self._class = _class
            self.locals = {}
            self.enable_setattr = () # value is irrelevant
            _class.resolve_attr('__init__')(self, *args, **kw)

        def __repr__(self):         # pragma: no cover
            return '<%s Instance at 0x%08x>' % (self._class.__name__, id(self))

        def __getattr__(self, name):
            # There are 4 cases for attribute lookup as seen in _PyObject_GenericGetattr:
            # 1. The attr is a data descriptor
            # 2. The attr is in the object's __dict__
            # 3. The attr is a non-data descriptor (usually a method)
            # 4. The attr is a non-descriptor somewhere up the MRO.
            try:
                val = self._class.resolve_attr(name)
                found = True
            except AttributeError:
                found = False
            else:
                # Case 1: it's a data descriptor!
                if hasattr(val, '__get__') and hasattr(val, '__set__'):
                    return val.__get__(self, name)

            # Case 2: the attr is in our __dict__ (that is, self.locals)
            if name in self.locals:
                return self.locals[name]

            if not found:
                raise AttributeError("%r object has no attribute %r" %
                                     (self._class.__name__, name))

            # Case 3: The attr is a non-data descriptor
            if hasattr(val, '__get__'):
                return val.__get__(self, self._class)

            # Case 4: Attr is a non-descriptor somewhere up the MRO
            return val

        def __setattr__(self, name, value):
            if not hasattr(self, 'enable_setattr'):
                return object.__setattr__(self, name, value)
            # TODO: deal with data descriptors
            self.locals[name] = value

class Method(object):
    def __init__(self, obj, _class, func):
        self.im_self = obj
        self.im_class = _class
        self.im_func = func

    def __repr__(self):         # pragma: no cover
        name = "%s.%s" % (self.im_class.__name__, self.im_func.func_name)
        if self.im_self is not None:
            return '<Bound Method %s of %s>' % (name, self.im_self)
        else:
            return '<Unbound Method %s>' % (name,)

    def __call__(self, *args, **kwargs):
        if self.im_self is not None:
            return self.im_func(self.im_self, *args, **kwargs)
        else:
            return self.im_func(*args, **kwargs)


class Cell(object):
    """A fake cell for closures.

    Closures keep names in scope by storing them not in a frame, but in a
    separate object called a cell.  Frames share references to cells, and
    the LOAD_DEREF and STORE_DEREF opcodes get and set the value from cells.

    This class acts as a cell, though it has to jump through two hoops to make
    the simulation complete:

        1. In order to create actual FunctionType functions, we have to have
           actual cell objects, which are difficult to make. See the twisty
           double-lambda in __init__.

        2. Actual cell objects can't be modified, so to implement STORE_DEREF,
           we store a one-element list in our cell, and then use [0] as the
           actual value.

    """
    def __init__(self, value):
        self.contents = value

    def get(self):
        return self.contents

    def set(self, value):
        self.contents = value


Block = collections.namedtuple("Block", "type, handler, level")


class Frame(object):
    def __init__(self, f_code, f_globals, f_locals, f_back):
        self.f_code = f_code
        self.f_globals = f_globals
        self.f_locals = f_locals
        self.f_back = f_back
        self.stack = []
        if f_back:
            self.f_builtins = f_back.f_builtins
        else:
            self.f_builtins = f_locals['__builtins__']
            if hasattr(self.f_builtins, '__dict__'):
                self.f_builtins = self.f_builtins.__dict__

        self.f_lineno = f_code.co_firstlineno
        self.f_lasti = 0

        if f_code.co_cellvars:
            self.cells = {}
            if not f_back.cells:
                f_back.cells = {}
            for var in f_code.co_cellvars:
                # Make a cell for the variable in our locals, or None.
                cell = Cell(self.f_locals.get(var))
                f_back.cells[var] = self.cells[var] = cell
        else:
            self.cells = None

        if f_code.co_freevars:
            if not self.cells:
                self.cells = {}
            for var in f_code.co_freevars:
                assert self.cells is not None
                assert f_back.cells, "f_back.cells: %r" % (f_back.cells,)
                self.cells[var] = f_back.cells[var]

        self.block_stack = []
        self.generator = None

    def __repr__(self):         # pragma: no cover
        return '<Frame at 0x%08x: %r @ %d>' % (
            id(self), self.f_code.co_filename, self.f_lineno
        )

    def line_number(self):
        """Get the current line number the frame is executing."""
        # We don't keep f_lineno up to date, so calculate it based on the
        # instruction address and the line number table.
        lnotab = self.f_code.co_lnotab
        byte_increments = six.iterbytes(lnotab[0::2])
        line_increments = six.iterbytes(lnotab[1::2])

        byte_num = 0
        line_num = self.f_code.co_firstlineno

        for byte_incr, line_incr in zip(byte_increments, line_increments):
            byte_num += byte_incr
            if byte_num > self.f_lasti:
                break
            line_num += line_incr

        return line_num


class Generator(object):
    def __init__(self, g_frame, vm):
        self.gi_frame = g_frame
        self.vm = vm
        self.first = True
        self.finished = False

    def __iter__(self):
        return self

    def next(self):
        # Ordinary iteration is like sending None into a generator.
        if not self.first:
            self.gi_frame.stack.append(None)
        self.first = False
        # To get the next value from an iterator, push its frame onto the
        # stack, and let it run.
        val = self.vm.resume_frame(self.gi_frame)
        if self.finished:
            raise StopIteration
        return val

    __next__ = next
