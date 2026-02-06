"""Core binder: walks tree-sitter CST and produces symbols, scopes, and references."""

from __future__ import annotations

import hashlib

from tree_sitter import Node

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.scope_stack import ScopeStack
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.location import Location, Position, Span
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind, TypeAnnotation, Visibility


class Binder:
    """Walks a tree-sitter CST and produces symbols, scopes, and references.

    Usage:
        adapter = PythonAdapter()
        tree = adapter.parse(source_bytes)
        binder = Binder(adapter, file_path, source_bytes)
        file_index = binder.bind(tree.root_node)
    """

    def __init__(self, adapter: PythonAdapter, file_path: str, source: bytes) -> None:
        self._adapter = adapter
        self._file_path = file_path
        self._source = source
        self._scope_stack = ScopeStack()
        self._symbols: list[Symbol] = []
        self._scopes: list[Scope] = []
        self._references: list[Reference] = []
        self._errors: list[str] = []
        # Track nodes that are being handled as part of a declaration
        # to avoid double-processing identifiers
        self._declaration_nodes: set[int] = set()

    def bind(self, root: Node) -> FileIndex:
        """Main entry point. Walk the CST and produce a FileIndex."""
        self._visit_module(root)
        return FileIndex(
            file_path=self._file_path,
            file_hash=self._compute_hash(),
            language=self._adapter.language_name,
            symbols=self._symbols,
            scopes=self._scopes,
            references=self._references,
            errors=self._errors,
        )

    # --- Module ---

    def _visit_module(self, node: Node) -> None:
        scope = Scope(
            scope_id=self._file_path,
            name=self._file_path,
            kind=ScopeKind.MODULE,
            file_path=self._file_path,
            span=self._make_span(node),
        )
        self._scopes.append(scope)
        self._scope_stack.push(scope)
        for child in node.children:
            self._visit_node(child)
        self._scope_stack.pop()

    # --- Dispatch ---

    def _visit_node(self, node: Node) -> None:
        """Dispatch to the appropriate handler based on node type."""
        node_type = node.type

        if node_type == "function_definition":
            self._visit_function_definition(node)
        elif node_type == "class_definition":
            self._visit_class_definition(node)
        elif node_type == "decorated_definition":
            self._visit_decorated_definition(node)
        elif node_type == "assignment":
            self._visit_assignment(node)
        elif node_type == "augmented_assignment":
            self._visit_augmented_assignment(node)
        elif node_type == "named_expression":
            self._visit_named_expression(node)
        elif node_type == "for_statement":
            self._visit_for_statement(node)
        elif node_type == "with_statement":
            self._visit_with_statement(node)
        elif node_type == "except_clause":
            self._visit_except_clause(node)
        elif node_type == "import_statement":
            self._visit_import_statement(node)
        elif node_type == "import_from_statement":
            self._visit_import_from_statement(node)
        elif node_type == "global_statement":
            self._visit_global_statement(node)
        elif node_type == "nonlocal_statement":
            self._visit_nonlocal_statement(node)
        elif node_type == "lambda":
            self._visit_lambda(node)
        elif node_type in (
            "list_comprehension",
            "set_comprehension",
            "dictionary_comprehension",
            "generator_expression",
        ):
            self._visit_comprehension(node)
        elif node_type == "identifier" and id(node) not in self._declaration_nodes:
            self._visit_identifier(node)
        elif node_type == "call":
            self._visit_call(node)
        else:
            # Recurse into children for any unhandled node type
            for child in node.children:
                self._visit_node(child)

    # --- Declarations ---

    def _visit_function_definition(
        self, node: Node, decorators: list[str] | None = None
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8")

        # Determine kind based on whether we're inside a class scope
        parent_scope = self._scope_stack.current_scope
        kind = SymbolKind.METHOD if parent_scope.kind == ScopeKind.CLASS else SymbolKind.FUNCTION

        visibility, vis_confidence = self._adapter.get_visibility(name)

        # Return type annotation
        return_type_node = node.child_by_field_name("return_type")
        type_ann = None
        if return_type_node:
            type_ann = TypeAnnotation(
                raw=return_type_node.text.decode("utf-8"),
                confidence=Confidence.DECLARED,
            )

        # Extract docstring from function body
        docstring = self._extract_docstring(node)

        symbol_id = self._scope_stack.build_symbol_id(
            self._file_path, name, is_scope_creator=True
        )
        symbol = Symbol(
            symbol_id=symbol_id,
            name=name,
            kind=kind,
            location=self._make_location(name_node),
            visibility=visibility,
            visibility_confidence=vis_confidence,
            type_annotation=type_ann,
            decorators=decorators or [],
            docstring=docstring,
            parent_scope_id=parent_scope.scope_id,
        )
        final_id = self._scope_stack.declare(name, symbol)
        self._symbols.append(symbol)
        parent_scope.symbol_ids.append(final_id)

        # Mark the name node as handled
        self._declaration_nodes.add(id(name_node))

        # Create function scope
        scope = Scope(
            scope_id=final_id,
            name=name,
            kind=ScopeKind.FUNCTION,
            file_path=self._file_path,
            span=self._make_span(node),
            parent_scope_id=parent_scope.scope_id,
        )
        self._scopes.append(scope)
        parent_scope.child_scope_ids.append(scope.scope_id)

        self._scope_stack.push(scope)

        # Extract parameters
        params_node = node.child_by_field_name("parameters")
        if params_node:
            self._visit_parameters(params_node)

        # Visit body
        body_node = node.child_by_field_name("body")
        if body_node:
            for child in body_node.children:
                self._visit_node(child)

        self._scope_stack.pop()

    def _visit_class_definition(
        self, node: Node, decorators: list[str] | None = None
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8")

        parent_scope = self._scope_stack.current_scope
        visibility, vis_confidence = self._adapter.get_visibility(name)
        docstring = self._extract_docstring(node)

        symbol_id = self._scope_stack.build_symbol_id(
            self._file_path, name, is_scope_creator=True
        )
        symbol = Symbol(
            symbol_id=symbol_id,
            name=name,
            kind=SymbolKind.CLASS,
            location=self._make_location(name_node),
            visibility=visibility,
            visibility_confidence=vis_confidence,
            decorators=decorators or [],
            docstring=docstring,
            parent_scope_id=parent_scope.scope_id,
        )
        final_id = self._scope_stack.declare(name, symbol)
        self._symbols.append(symbol)
        parent_scope.symbol_ids.append(final_id)

        self._declaration_nodes.add(id(name_node))

        # Visit base classes as references
        superclasses_node = node.child_by_field_name("superclasses")
        if superclasses_node:
            for child in superclasses_node.children:
                self._visit_node(child)

        # Create class scope
        scope = Scope(
            scope_id=final_id,
            name=name,
            kind=ScopeKind.CLASS,
            file_path=self._file_path,
            span=self._make_span(node),
            parent_scope_id=parent_scope.scope_id,
        )
        self._scopes.append(scope)
        parent_scope.child_scope_ids.append(scope.scope_id)

        self._scope_stack.push(scope)

        # Visit body
        body_node = node.child_by_field_name("body")
        if body_node:
            for child in body_node.children:
                self._visit_node(child)

        self._scope_stack.pop()

    def _visit_decorated_definition(self, node: Node) -> None:
        """Extract decorators, then visit the inner function/class definition."""
        decorators: list[str] = []
        definition_node = None

        for child in node.children:
            if child.type == "decorator":
                dec_text = child.text.decode("utf-8").lstrip("@").strip()
                decorators.append(dec_text)
                # Visit decorator expression for references
                for dec_child in child.children:
                    if dec_child.type != "@":
                        self._visit_node(dec_child)
            elif child.type == "function_definition":
                definition_node = child
            elif child.type == "class_definition":
                definition_node = child

        if definition_node:
            if definition_node.type == "function_definition":
                self._visit_function_definition(definition_node, decorators=decorators)
            elif definition_node.type == "class_definition":
                self._visit_class_definition(definition_node, decorators=decorators)

    def _visit_assignment(self, node: Node) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        type_node = node.child_by_field_name("type")

        type_ann = None
        if type_node:
            type_ann = TypeAnnotation(
                raw=type_node.text.decode("utf-8"),
                confidence=Confidence.DECLARED,
            )

        if left:
            targets = self._extract_targets(left)
            for target_node in targets:
                name = target_node.text.decode("utf-8")
                self._declare_variable(target_node, name, type_ann)

        # Visit right side for references
        if right:
            self._visit_node(right)
        # Visit type annotation for references
        if type_node:
            self._visit_node(type_node)

    def _visit_augmented_assignment(self, node: Node) -> None:
        """Handle x += expr. This is a read+write, not a new declaration."""
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")

        if left and left.type == "identifier":
            name = left.text.decode("utf-8")
            self._declaration_nodes.add(id(left))
            resolved = self._scope_stack.resolve(name)
            if resolved:
                # Write reference
                self._references.append(
                    Reference(
                        symbol_id=resolved.symbol_id,
                        kind=ReferenceKind.WRITE,
                        location=self._make_location(left),
                        in_scope_id=self._scope_stack.current_scope.scope_id,
                    )
                )
            else:
                self._references.append(
                    Reference(
                        symbol_id=name,
                        kind=ReferenceKind.WRITE,
                        location=self._make_location(left),
                        in_scope_id=self._scope_stack.current_scope.scope_id,
                        resolved=False,
                    )
                )

        if right:
            self._visit_node(right)

    def _visit_named_expression(self, node: Node) -> None:
        """Handle walrus operator x := expr.

        In comprehensions, the target binds in the containing function scope.
        """
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")

        if name_node:
            name = name_node.text.decode("utf-8")
            self._declaration_nodes.add(id(name_node))

            # If inside a comprehension, bind in the enclosing function scope
            current_kind = self._scope_stack.current_scope.kind
            if current_kind == ScopeKind.COMPREHENSION:
                target_entry = self._scope_stack.find_enclosing_function_entry()
                if target_entry:
                    symbol_id = self._build_symbol_id_for_scope(
                        target_entry.scope, name
                    )
                    symbol = self._make_variable_symbol(name_node, name, symbol_id)
                    self._scope_stack.declare_in_scope(name, symbol, target_entry)
                    self._symbols.append(symbol)
                    target_entry.scope.symbol_ids.append(symbol.symbol_id)
            else:
                self._declare_variable(name_node, name)

        if value_node:
            self._visit_node(value_node)

    def _visit_for_statement(self, node: Node) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        body = node.child_by_field_name("body")
        alternative = node.child_by_field_name("alternative")

        if left:
            targets = self._extract_targets(left)
            for target_node in targets:
                name = target_node.text.decode("utf-8")
                self._declare_variable(target_node, name)

        if right:
            self._visit_node(right)
        if body:
            for child in body.children:
                self._visit_node(child)
        if alternative:
            for child in alternative.children:
                self._visit_node(child)

    def _visit_with_statement(self, node: Node) -> None:
        body = node.child_by_field_name("body")

        # with_statement has with_clause children containing with_item nodes
        for child in node.children:
            if child.type == "with_clause":
                for with_item in child.children:
                    if with_item.type == "with_item":
                        self._visit_with_item(with_item)

        if body:
            for child in body.children:
                self._visit_node(child)

    def _visit_with_item(self, node: Node) -> None:
        """Handle a single with_item: expression [as target]."""
        for child in node.children:
            if child.type == "as_pattern":
                # as_pattern: expression "as" as_pattern_target
                for ap_child in child.children:
                    if ap_child.type == "as_pattern_target":
                        for target_child in ap_child.children:
                            if target_child.type == "identifier":
                                name = target_child.text.decode("utf-8")
                                self._declare_variable(target_child, name)
                    elif ap_child.type not in ("as",):
                        # Visit the expression for references
                        self._visit_node(ap_child)
            else:
                # No 'as' clause — just visit the expression
                self._visit_node(child)

    def _visit_except_clause(self, node: Node) -> None:
        """Extract exception variable from except clause."""
        for child in node.children:
            if child.type == "block":
                for block_child in child.children:
                    self._visit_node(block_child)
            elif child.type == "as_pattern":
                # as_pattern contains: exception_type, "as", as_pattern_target
                for ap_child in child.children:
                    if ap_child.type == "as_pattern_target":
                        for target_child in ap_child.children:
                            if target_child.type == "identifier":
                                name = target_child.text.decode("utf-8")
                                self._declare_variable(target_child, name)
                    elif ap_child.type == "identifier":
                        # The exception type — visit as reference
                        self._visit_node(ap_child)
            elif child.type not in ("except", ":", ","):
                self._visit_node(child)

    def _visit_import_statement(self, node: Node) -> None:
        """Handle `import x` and `import x as y`."""
        for child in node.children:
            if child.type == "dotted_name":
                name = child.text.decode("utf-8")
                self._declare_import(child, name, name)
            elif child.type == "aliased_import":
                module_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if module_node and alias_node:
                    self._declare_import(
                        alias_node,
                        alias_node.text.decode("utf-8"),
                        module_node.text.decode("utf-8"),
                    )
                elif module_node:
                    name = module_node.text.decode("utf-8")
                    self._declare_import(module_node, name, name)

    def _visit_import_from_statement(self, node: Node) -> None:
        """Handle `from x import y` and `from x import y as z`."""
        module_name = ""
        module_node = node.child_by_field_name("module_name")
        if module_node:
            module_name = module_node.text.decode("utf-8")

        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                name = child.text.decode("utf-8")
                self._declare_import(child, name, f"{module_name}.{name}")
            elif child.type == "aliased_import":
                import_name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if import_name_node and alias_node:
                    self._declare_import(
                        alias_node,
                        alias_node.text.decode("utf-8"),
                        f"{module_name}.{import_name_node.text.decode('utf-8')}",
                    )
                elif import_name_node:
                    name = import_name_node.text.decode("utf-8")
                    self._declare_import(
                        import_name_node, name, f"{module_name}.{name}"
                    )
            elif child.type == "identifier" and child != module_node:
                # Direct identifier import (e.g., `from os import path`)
                if child.prev_sibling and child.prev_sibling.type == "import":
                    name = child.text.decode("utf-8")
                    self._declare_import(child, name, f"{module_name}.{name}")

    def _visit_global_statement(self, node: Node) -> None:
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode("utf-8")
                self._scope_stack.current.globals_declared.add(name)
                self._declaration_nodes.add(id(child))

    def _visit_nonlocal_statement(self, node: Node) -> None:
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode("utf-8")
                self._scope_stack.current.nonlocals_declared.add(name)
                self._declaration_nodes.add(id(child))

    # --- Scope-creating constructs ---

    def _visit_lambda(self, node: Node) -> None:
        parent_scope = self._scope_stack.current_scope
        scope = Scope(
            scope_id=f"{self._scope_stack.build_scope_chain(self._file_path)}:<lambda:{node.start_point[0]}>",
            name="<lambda>",
            kind=ScopeKind.LAMBDA,
            file_path=self._file_path,
            span=self._make_span(node),
            parent_scope_id=parent_scope.scope_id,
        )
        self._scopes.append(scope)
        parent_scope.child_scope_ids.append(scope.scope_id)

        self._scope_stack.push(scope)

        # Extract parameters
        params_node = node.child_by_field_name("parameters")
        if params_node:
            self._visit_parameters(params_node)

        # Visit body
        body_node = node.child_by_field_name("body")
        if body_node:
            self._visit_node(body_node)

        self._scope_stack.pop()

    def _visit_comprehension(self, node: Node) -> None:
        parent_scope = self._scope_stack.current_scope

        scope = Scope(
            scope_id=f"{self._scope_stack.build_scope_chain(self._file_path)}:<comp:{node.start_point[0]}>",
            name="<comprehension>",
            kind=ScopeKind.COMPREHENSION,
            file_path=self._file_path,
            span=self._make_span(node),
            parent_scope_id=parent_scope.scope_id,
        )
        self._scopes.append(scope)
        parent_scope.child_scope_ids.append(scope.scope_id)

        self._scope_stack.push(scope)

        # Visit children — for_in_clause contains loop variable and iterable
        first_for = True
        for child in node.children:
            if child.type == "for_in_clause":
                left = child.child_by_field_name("left")
                right = child.child_by_field_name("right")
                if first_for and right:
                    # First iterable is evaluated in enclosing scope
                    self._scope_stack.pop()
                    self._visit_node(right)
                    self._scope_stack.push(scope)
                    first_for = False
                elif right:
                    self._visit_node(right)
                if left:
                    targets = self._extract_targets(left)
                    for target_node in targets:
                        name = target_node.text.decode("utf-8")
                        self._declare_variable(target_node, name)
            elif child.type == "if_clause":
                for if_child in child.children:
                    if if_child.type != "if":
                        self._visit_node(if_child)
            elif child.type not in ("[", "]", "{", "}", "(", ")"):
                self._visit_node(child)

        self._scope_stack.pop()

    # --- References ---

    def _visit_identifier(self, node: Node) -> None:
        """Handle an identifier that is not in a declaration context."""
        if id(node) in self._declaration_nodes:
            return

        name = node.text.decode("utf-8")

        # Skip keywords that tree-sitter might parse as identifiers
        if name in ("True", "False", "None"):
            return

        resolved = self._scope_stack.resolve(name)
        ref_kind = self._determine_reference_kind(node)

        if resolved:
            self._references.append(
                Reference(
                    symbol_id=resolved.symbol_id,
                    kind=ref_kind,
                    location=self._make_location(node),
                    in_scope_id=self._scope_stack.current_scope.scope_id,
                )
            )
        else:
            self._references.append(
                Reference(
                    symbol_id=name,
                    kind=ref_kind,
                    location=self._make_location(node),
                    in_scope_id=self._scope_stack.current_scope.scope_id,
                    resolved=False,
                )
            )

    def _visit_call(self, node: Node) -> None:
        """Handle function calls — the function name gets a CALL reference."""
        function_node = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")

        if function_node:
            if function_node.type == "identifier":
                name = function_node.text.decode("utf-8")
                self._declaration_nodes.add(id(function_node))
                resolved = self._scope_stack.resolve(name)
                if resolved:
                    self._references.append(
                        Reference(
                            symbol_id=resolved.symbol_id,
                            kind=ReferenceKind.CALL,
                            location=self._make_location(function_node),
                            in_scope_id=self._scope_stack.current_scope.scope_id,
                        )
                    )
                else:
                    self._references.append(
                        Reference(
                            symbol_id=name,
                            kind=ReferenceKind.CALL,
                            location=self._make_location(function_node),
                            in_scope_id=self._scope_stack.current_scope.scope_id,
                            resolved=False,
                        )
                    )
            else:
                # Attribute call like obj.method() — visit normally for references
                self._visit_node(function_node)

        if args_node:
            for child in args_node.children:
                self._visit_node(child)

    # --- Parameters ---

    def _visit_parameters(self, node: Node) -> None:
        """Extract parameters from a function's parameter list."""
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode("utf-8")
                self._declare_parameter(child, name)
            elif child.type in ("default_parameter", "typed_default_parameter"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    type_node = child.child_by_field_name("type")
                    type_ann = None
                    if type_node:
                        type_ann = TypeAnnotation(
                            raw=type_node.text.decode("utf-8"),
                            confidence=Confidence.DECLARED,
                        )
                    self._declare_parameter(name_node, name, type_ann)
                    self._declaration_nodes.add(id(name_node))
                # Visit default value for references
                value_node = child.child_by_field_name("value")
                if value_node:
                    self._visit_node(value_node)
            elif child.type == "typed_parameter":
                # typed_parameter has identifier child (not 'name' field)
                name_node = None
                for tc in child.children:
                    if tc.type == "identifier":
                        name_node = tc
                        break
                if name_node:
                    name = name_node.text.decode("utf-8")
                    type_node = child.child_by_field_name("type")
                    type_ann = None
                    if type_node:
                        type_ann = TypeAnnotation(
                            raw=type_node.text.decode("utf-8"),
                            confidence=Confidence.DECLARED,
                        )
                    self._declare_parameter(name_node, name, type_ann)
                    self._declaration_nodes.add(id(name_node))
            elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                # *args, **kwargs
                for splat_child in child.children:
                    if splat_child.type == "identifier":
                        name = splat_child.text.decode("utf-8")
                        self._declare_parameter(splat_child, name)
                        self._declaration_nodes.add(id(splat_child))

    # --- Helpers ---

    def _declare_variable(
        self,
        node: Node,
        name: str,
        type_ann: TypeAnnotation | None = None,
    ) -> None:
        """Declare a variable in the current or redirected scope."""
        self._declaration_nodes.add(id(node))
        current_entry = self._scope_stack.current

        # Check global/nonlocal redirects
        if name in current_entry.globals_declared:
            target_entry = self._scope_stack.find_global_target()
            symbol_id = self._build_symbol_id_for_scope(target_entry.scope, name)
            symbol = self._make_variable_symbol(node, name, symbol_id, type_ann)
            symbol.parent_scope_id = target_entry.scope.scope_id
            self._scope_stack.declare_in_scope(name, symbol, target_entry)
            self._symbols.append(symbol)
            target_entry.scope.symbol_ids.append(symbol.symbol_id)
            return

        if name in current_entry.nonlocals_declared:
            target_entry = self._scope_stack.find_nonlocal_target(name)
            if target_entry:
                symbol_id = self._build_symbol_id_for_scope(target_entry.scope, name)
                symbol = self._make_variable_symbol(node, name, symbol_id, type_ann)
                symbol.parent_scope_id = target_entry.scope.scope_id
                self._scope_stack.declare_in_scope(name, symbol, target_entry)
                self._symbols.append(symbol)
                target_entry.scope.symbol_ids.append(symbol.symbol_id)
                return

        scope = self._scope_stack.current_scope
        symbol_id = self._scope_stack.build_symbol_id(self._file_path, name)
        symbol = self._make_variable_symbol(node, name, symbol_id, type_ann)
        symbol.parent_scope_id = scope.scope_id
        final_id = self._scope_stack.declare(name, symbol)
        self._symbols.append(symbol)
        scope.symbol_ids.append(final_id)

    def _declare_parameter(
        self,
        node: Node,
        name: str,
        type_ann: TypeAnnotation | None = None,
    ) -> None:
        self._declaration_nodes.add(id(node))
        scope = self._scope_stack.current_scope
        visibility, vis_confidence = self._adapter.get_visibility(name)
        symbol_id = self._scope_stack.build_symbol_id(self._file_path, name)

        symbol = Symbol(
            symbol_id=symbol_id,
            name=name,
            kind=SymbolKind.PARAMETER,
            location=self._make_location(node),
            visibility=visibility,
            visibility_confidence=vis_confidence,
            type_annotation=type_ann,
            parent_scope_id=scope.scope_id,
        )
        final_id = self._scope_stack.declare(name, symbol)
        self._symbols.append(symbol)
        scope.symbol_ids.append(final_id)

    def _declare_import(
        self, node: Node, local_name: str, module_path: str
    ) -> None:
        self._declaration_nodes.add(id(node))
        scope = self._scope_stack.current_scope
        visibility, vis_confidence = self._adapter.get_visibility(local_name)
        symbol_id = self._scope_stack.build_symbol_id(self._file_path, local_name)

        symbol = Symbol(
            symbol_id=symbol_id,
            name=local_name,
            kind=SymbolKind.IMPORT,
            location=self._make_location(node),
            visibility=visibility,
            visibility_confidence=vis_confidence,
            parent_scope_id=scope.scope_id,
        )
        final_id = self._scope_stack.declare(local_name, symbol)
        self._symbols.append(symbol)
        scope.symbol_ids.append(final_id)

    def _make_variable_symbol(
        self,
        node: Node,
        name: str,
        symbol_id: str,
        type_ann: TypeAnnotation | None = None,
    ) -> Symbol:
        visibility, vis_confidence = self._adapter.get_visibility(name)
        return Symbol(
            symbol_id=symbol_id,
            name=name,
            kind=SymbolKind.VARIABLE,
            location=self._make_location(node),
            visibility=visibility,
            visibility_confidence=vis_confidence,
            type_annotation=type_ann,
        )

    def _build_symbol_id_for_scope(self, scope: Scope, name: str) -> str:
        """Build a symbol ID for a specific scope (used for global/nonlocal redirects)."""
        if scope.kind == ScopeKind.MODULE:
            return f"{self._file_path}:{name}"
        return f"{scope.scope_id}:{name}"

    def _extract_targets(self, node: Node) -> list[Node]:
        """Extract assignment targets, handling tuple unpacking.

        Only extracts identifier targets. Attribute and subscript targets
        are skipped (they create references, not declarations).
        """
        targets: list[Node] = []
        if node.type == "identifier":
            targets.append(node)
        elif node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
            for child in node.children:
                targets.extend(self._extract_targets(child))
        elif node.type in ("tuple", "list"):
            for child in node.children:
                targets.extend(self._extract_targets(child))
        elif node.type == "list_splat_pattern":
            for child in node.children:
                if child.type == "identifier":
                    targets.append(child)
        # Attribute and subscript targets are visited as references elsewhere
        return targets

    def _extract_docstring(self, node: Node) -> str | None:
        """Extract docstring from a function or class definition."""
        body = node.child_by_field_name("body")
        if not body or not body.children:
            return None
        first = body.children[0]
        if first.type == "expression_statement" and first.children:
            string_node = first.children[0]
            if string_node.type == "string":
                text = string_node.text.decode("utf-8")
                # Strip quotes
                if text.startswith('"""') or text.startswith("'''"):
                    return text[3:-3].strip()
                elif text.startswith('"') or text.startswith("'"):
                    return text[1:-1].strip()
        return None

    def _determine_reference_kind(self, node: Node) -> ReferenceKind:
        """Determine the kind of reference from the node's context."""
        parent = node.parent
        if parent is None:
            return ReferenceKind.READ

        if parent.type == "decorator":
            return ReferenceKind.DECORATOR
        if parent.type == "type":
            return ReferenceKind.TYPE_ANNOTATION
        if parent.type == "call" and node == parent.child_by_field_name("function"):
            return ReferenceKind.CALL

        return ReferenceKind.READ

    def _make_location(self, node: Node) -> Location:
        return Location(
            file_path=self._file_path,
            span=self._make_span(node),
        )

    def _make_span(self, node: Node) -> Span:
        return Span(
            start=Position(line=node.start_point[0], column=node.start_point[1]),
            end=Position(line=node.end_point[0], column=node.end_point[1]),
        )

    def _compute_hash(self) -> str:
        return hashlib.sha256(self._source).hexdigest()
