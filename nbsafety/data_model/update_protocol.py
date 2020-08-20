# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Set
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.data_model.scope import NamespaceScope
    from nbsafety.safety import NotebookSafety

logger = logging.getLogger(__name__)


class UpdateProtocol(object):
    def __init__(self, safety: 'NotebookSafety', updated_sym: 'DataSymbol', mutated: bool):
        self.safety = safety
        self.updated_sym = updated_sym
        self.mutated = mutated
        self.seen: Set[DataSymbol] = set()

    def __call__(self, propagate=True):
        if propagate:
            self._collect_updated_symbols(self.updated_sym)
        self.safety.updated_symbols = set(self.seen)
        for dsym in self.safety.updated_symbols:
            self._propagate_staleness_to_deps(dsym, skip_seen_check=True)
        # important! don't bump defined_cell_num until the very end!
        #  need to wait until here because, by default,
        #  we don't want to propagate to symbols defined in the same cell
        self.updated_sym.defined_cell_num = cell_counter()
        self.updated_sym.fresher_ancestors.clear()
        self.updated_sym.namespace_stale_symbols.clear()

    def _collect_updated_symbols(self, dsym: 'DataSymbol'):
        if dsym in self.seen:
            return
        self.seen.add(dsym)
        for dsym_alias in self.safety.aliases[dsym.obj_id]:
            containing_scope: 'NamespaceScope' = cast('NamespaceScope', dsym_alias.containing_scope)
            if not containing_scope.is_namespace_scope:
                continue
            # TODO: figure out what this is for again
            # self.safety.updated_scopes.add(containing_scope)
            containing_scope.max_defined_timestamp = cell_counter()
            containing_namespace_obj_id = containing_scope.obj_id
            for alias in self.safety.aliases[containing_namespace_obj_id]:
                alias.namespace_stale_symbols.discard(dsym)
                self._collect_updated_symbols(alias)

    def _propagate_staleness_to_namespace_parents(self, dsym: 'DataSymbol', skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        containing_scope: 'NamespaceScope' = cast('NamespaceScope', dsym.containing_scope)
        if containing_scope is None or not containing_scope.is_namespace_scope:
            return
        for containing_alias in self.safety.aliases[containing_scope.obj_id]:
            containing_alias.namespace_stale_symbols.add(dsym)
            self._propagate_staleness_to_namespace_parents(containing_alias)
            for child in self._non_class_to_instance_children(containing_alias):
                # print('propagate from', dsym, 'to', child)
                self._propagate_staleness_to_deps(child)

    def _non_class_to_instance_children(self, dsym):
        if self.updated_sym == dsym:
            yield from dsym.children
            return
        for child in dsym.children:
            # Next, complicated check to avoid propagating along a class -> instance edge.
            # The only time this is OK is when we changed the class, which will not be the case here.
            child_namespace = child.namespace
            if child_namespace is not None and child_namespace.cloned_from is not None:
                if child_namespace.cloned_from.obj_id == dsym.obj_id:
                    continue
            yield child

    def _propagate_staleness_to_namespace_children(self, dsym: 'DataSymbol', skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        self_scope = self.safety.namespaces.get(dsym.obj_id, None)
        if self_scope is None:
            return
        for ns_child in self_scope.all_data_symbols_this_indentation(exclude_class=True):
            # print('propagate from', dsym, 'to namespace child', ns_child)
            self._propagate_staleness_to_deps(ns_child)

    def _propagate_staleness_to_deps(self, dsym: 'DataSymbol', skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        if dsym not in self.safety.updated_symbols:
            if dsym.should_mark_stale(self.updated_sym):
                dsym.fresher_ancestors.add(self.updated_sym)
                dsym.required_cell_num = cell_counter()
                self._propagate_staleness_to_namespace_parents(dsym, skip_seen_check=True)
                self._propagate_staleness_to_namespace_children(dsym, skip_seen_check=True)
        for child in self._non_class_to_instance_children(dsym):
            # print('propagate from', dsym, 'to', child)
            self._propagate_staleness_to_deps(child)