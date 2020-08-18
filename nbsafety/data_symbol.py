# -*- coding: utf-8 -*-
import ast
from enum import Enum
import logging
from typing import cast, TYPE_CHECKING
import weakref

from .ipython_utils import cell_counter
from .legacy_update_protocol import LegacyUpdateProtocol

if TYPE_CHECKING:
    from typing import Any, Optional, Set, Union
    import ast
    from .safety import NotebookSafety
    from .scope import Scope, NamespaceScope

logger = logging.getLogger(__name__)


class DataSymbolType(Enum):
    DEFAULT = 'default'
    SUBSCRIPT = 'subscript'
    FUNCTION = 'function'
    CLASS = 'class'


class DataSymbol(object):
    def __init__(
            self,
            name: 'Union[str, int]',
            symbol_type: 'DataSymbolType',
            obj: 'Any',
            containing_scope: 'Scope',
            safety: 'NotebookSafety',
            stmt_node: 'Optional[ast.AST]' = None,
            parents: 'Optional[Set[DataSymbol]]' = None,
            refresh_cached_obj=False,
    ):
        # print(containing_scope, name, obj, is_subscript)
        self.name = name
        self.symbol_type = symbol_type
        tombstone, obj_ref, has_weakref = self._update_obj_ref_inner(obj)
        self._tombstone = tombstone
        self._obj_ref = obj_ref
        self._has_weakref = has_weakref
        self.cached_obj_ref = None
        self._cached_has_weakref = None
        self.cached_obj_id = None
        self.cached_obj_type = None
        if refresh_cached_obj:
            self._refresh_cached_obj()
        self.containing_scope = containing_scope
        self.safety = safety
        self.stmt_node = self.update_stmt_node(stmt_node)
        self._funcall_live_symbols = None
        if parents is None:
            parents = set()
        self.parents: Set[DataSymbol] = parents
        self.children: Set[DataSymbol] = set()
        self.readable_name = containing_scope.make_namespace_qualified_name(self)

        self.call_scope: 'Optional[Scope]' = None
        if self.is_function:
            self.call_scope = self.containing_scope.make_child_scope(self.name)

        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        self.fresher_ancestors: Set[DataSymbol] = set()
        self.namespace_stale_symbols: Set[DataSymbol] = set()

        # Will never be stale if no_warning is True
        self.disable_warnings = False

    def __repr__(self):
        return f'<{self.readable_name}>'

    def __str__(self):
        return self.readable_name

    def __hash__(self):
        return hash(self.full_path)

    @property
    def is_subscript(self):
        return self.symbol_type == DataSymbolType.SUBSCRIPT

    @property
    def is_class(self):
        return self.symbol_type == DataSymbolType.CLASS

    @property
    def is_function(self):
        return self.symbol_type == DataSymbolType.FUNCTION

    def _get_obj(self) -> 'Any':
        if self._has_weakref:
            return self._obj_ref()
        else:
            return self._obj_ref

    def _get_cached_obj(self) -> 'Any':
        if self._cached_has_weakref:
            return self.cached_obj_ref()
        else:
            return self.cached_obj_ref

    def shallow_clone(self, new_obj, new_containing_scope, symbol_type):
        return self.__class__(self.name, symbol_type, new_obj, new_containing_scope, self.safety)

    @property
    def obj_id(self):
        return id(self._get_obj())

    @property
    def obj_type(self):
        return type(self._get_obj())

    @property
    def namespace(self):
        return self.safety.namespaces.get(self.obj_id, None)

    @property
    def full_path(self):
        return self.containing_scope.full_path + (self.name,)

    @property
    def full_namespace_path(self):
        return self.containing_scope.make_namespace_qualified_name(self)

    @property
    def is_garbage(self):
        return (
            self._tombstone
            or self.containing_scope.is_garbage
            or not self.containing_scope.is_globally_accessible
            or (self._has_weakref and self._get_obj() is None)
        )

    @property
    def is_globally_accessible(self):
        return self.containing_scope.is_globally_accessible

    def _obj_reference_expired_callback(self, *_):
        # just write a tombstone here; we'll do a batch collect after the main part of the cell is done running
        # can potentially support GC in the background further down the line
        self._tombstone = True

    def collect_self_garbage(self):
        for parent in self.parents:
            parent.children.discard(self)
        for child in self.children:
            child.parents.discard(self)
        self_aliases = self.safety.aliases.get(self.cached_obj_id, None)
        if self_aliases is not None:
            self_aliases.discard(self)
            if len(self_aliases) == 0:
                # kill the alias but leave the namespace
                # namespace needs to stick around to properly handle the staleness propagation protocol
                self.safety.aliases.pop(self.cached_obj_id, None)

    def update_type(self, new_type):
        self.symbol_type = new_type
        if self.is_function:
            self.call_scope = self.containing_scope.make_child_scope(self.name)
        else:
            self.call_scope = None

    def update_obj_ref(self, obj):
        tombstone, obj_ref, has_weakref = self._update_obj_ref_inner(obj)
        self._tombstone = tombstone
        self._obj_ref = obj_ref
        self._has_weakref = has_weakref

    def _update_obj_ref_inner(self, obj):
        tombstone = False
        try:
            obj_ref = weakref.ref(obj, self._obj_reference_expired_callback)
            has_weakref = True
        except TypeError:
            obj_ref = obj
            has_weakref = False
        return tombstone, obj_ref, has_weakref

    def update_stmt_node(self, stmt_node):
        self.stmt_node = stmt_node
        self._funcall_live_symbols = None
        if self.is_function:
            self.safety.statement_to_func_cell[id(stmt_node)] = self
        return stmt_node

    def _refresh_cached_obj(self):
        self.cached_obj_ref = self._obj_ref
        self.cached_obj_id = self.obj_id
        self.cached_obj_type = self.obj_type
        self._cached_has_weakref = self._has_weakref

    def get_call_args(self):
        # TODO: handle lambda, objects w/ __call__, etc
        args = set()
        if self.is_function:
            assert isinstance(self.stmt_node, ast.FunctionDef)
            for arg in self.stmt_node.args.args + self.stmt_node.args.kwonlyargs:
                args.add(arg.arg)
            if self.stmt_node.args.vararg is not None:
                args.add(self.stmt_node.args.vararg.arg)
            if self.stmt_node.args.kwarg is not None:
                args.add(self.stmt_node.args.kwarg.arg)
        return args

    def create_symbols_for_call_args(self):
        for arg in self.get_call_args():
            # TODO: ideally we should try to pass the object here
            self.call_scope.upsert_data_symbol_for_name(arg, None, set(), self.stmt_node, False, propagate=False)

    @property
    def is_stale(self):
        if self.disable_warnings:
            return False
        return self.defined_cell_num < self.required_cell_num or len(self.namespace_stale_symbols) > 0

    def should_mark_stale(self, updated_dep):
        if self.disable_warnings:
            return False
        if updated_dep is self:
            return False
        should_mark_stale = not self.safety.config.no_stale_propagation_for_same_cell_definition
        should_mark_stale = should_mark_stale or updated_dep.defined_cell_num != self.defined_cell_num
        return should_mark_stale

    def update_deps(self, new_deps: 'Set[DataSymbol]', overwrite=True, mutated=False, propagate=True):
        update_protocol = LegacyUpdateProtocol(self.safety, self, new_deps, mutated)
        update_protocol(overwrite=overwrite, propagate=propagate)

    def refresh(self: 'DataSymbol'):
        self.fresher_ancestors = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        self.namespace_stale_symbols = set()
        self._propagate_refresh_to_namespace_parents(set())
        # seen = set()
        # for alias in self.safety.aliases[self.obj_id]:
        #     alias._propagate_refresh_to_namespace_parents(seen)

    def _propagate_refresh_to_namespace_parents(self, seen: 'Set[DataSymbol]'):
        if self in seen:
            return
        # print('refresh propagate', self)
        seen.add(self)
        for self_alias in self.safety.aliases[self.obj_id]:
            containing_scope: 'NamespaceScope' = cast('NamespaceScope', self_alias.containing_scope)
            if not containing_scope.is_namespace_scope:
                continue
            # if containing_scope.max_defined_timestamp == cell_counter():
            #     return
            containing_scope.max_defined_timestamp = cell_counter()
            containing_namespace_obj_id = containing_scope.obj_id
            # print('containing namespaces:', self.safety.aliases[containing_namespace_obj_id])
            for alias in self.safety.aliases[containing_namespace_obj_id]:
                alias.namespace_stale_symbols.discard(self)
                if not alias.is_stale:
                    alias.defined_cell_num = cell_counter()
                    alias.fresher_ancestors = set()
                # print('working on', alias, '; stale?', alias.is_stale, alias.namespace_stale_symbols)
                alias._propagate_refresh_to_namespace_parents(seen)
