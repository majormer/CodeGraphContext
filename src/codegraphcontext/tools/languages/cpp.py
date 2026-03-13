
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from codegraphcontext.utils.debug_log import debug_log, info_logger, error_logger, warning_logger
from codegraphcontext.utils.tree_sitter_manager import execute_query

# ---------------------------------------------------------------------------
# UE5 macro patterns to strip before tree-sitter parsing.
# These macros confuse the C++ grammar (e.g. SMARTFOUNDATIONS_API becomes the
# class name, UCLASS() creates ERROR nodes, GENERATED_BODY() breaks bodies).
# Replacements preserve byte length so line/column numbers stay correct.
# ---------------------------------------------------------------------------
_UE5_API_MACRO_RE = re.compile(r'\b[A-Z][A-Z0-9_]*_API\b')
_UE5_DECORATOR_RE = re.compile(
    r'^\s*(?:UCLASS|USTRUCT|UENUM|UINTERFACE|UFUNCTION|UPROPERTY|UMETA|UPARAM)'
    r'\s*\([^)]*\)\s*$', re.MULTILINE)
_UE5_GENERATED_RE = re.compile(
    r'^\s*(?:GENERATED_BODY|GENERATED_USTRUCT_BODY|GENERATED_UCLASS_BODY'
    r'|GENERATED_UINTERFACE_BODY)\s*\(\s*\)\s*$', re.MULTILINE)
_UE5_INLINE_DECORATOR_RE = re.compile(
    r'(?:UFUNCTION|UPROPERTY|UMETA|UPARAM)\s*\([^)]*\)\s*')


def _strip_ue5_macros(source: str) -> str:
    """Replace UE5 macros with equal-length whitespace to preserve offsets."""
    source = _UE5_DECORATOR_RE.sub(lambda m: ' ' * len(m.group()), source)
    source = _UE5_GENERATED_RE.sub(lambda m: ' ' * len(m.group()), source)
    source = _UE5_INLINE_DECORATOR_RE.sub(lambda m: ' ' * len(m.group()), source)
    source = _UE5_API_MACRO_RE.sub(lambda m: ' ' * len(m.group()), source)
    return source


# ---------------------------------------------------------------------------
# Tree-sitter queries  (UE5-aware: includes qualified_identifier etc.)
# ---------------------------------------------------------------------------
CPP_QUERIES = {
    "functions": """
        (function_definition
            declarator: (function_declarator
                declarator: [
                    (identifier) @name
                    (field_identifier) @name
                    (qualified_identifier) @name
                    (destructor_name) @name
                ]
            )
        ) @function_node
    """,
    "classes": """
        (class_specifier
            name: (type_identifier) @name
        ) @class
    """,
    "imports": """
        (preproc_include
            path: [
                (string_literal) @path
                (system_lib_string) @path
            ]
        ) @import
    """,
    "calls": """
        (call_expression
            function: [
                (identifier) @function_name
                (field_expression
                    field: (field_identifier) @method_name
                )
                (qualified_identifier) @qualified_call
                (template_function
                    name: (identifier) @template_call
                )
            ]
            arguments: (argument_list) @args
        )
    """,
    "enums": """
        (enum_specifier
            name: (type_identifier) @name
            body: (enumerator_list
                (enumerator
                    name: (identifier) @value
                    )*
                )? @body
        ) @enum
    """,
    "structs": """
        (struct_specifier
            name: (type_identifier) @name
            body: (field_declaration_list)? @body
        ) @struct
    """,
    "unions": """
        (union_specifier
            name: (type_identifier)? @name
            body: (field_declaration_list
                (field_declaration
                    declarator: [
                        (field_identifier) @value
                        (pointer_declarator (field_identifier) @value)
                        (array_declarator (field_identifier) @value)
                    ]
                )*
            )? @body
        ) @union
    """,
    "macros": """
        (preproc_def
            name: (identifier) @name
        ) @macro
    """,
    "variables": """
        (declaration
            declarator: (init_declarator
                            declarator: (identifier) @name))

        (declaration
            declarator: (init_declarator
                            declarator: (pointer_declarator
                                declarator: (identifier) @name)))

        (field_declaration
            declarator: [
                 (field_identifier) @name
                 (pointer_declarator declarator: (field_identifier) @name)
                 (array_declarator declarator: (field_identifier) @name)
                 (reference_declarator (field_identifier) @name)
            ]
        )
    """,
    "lambda_assignments": """
        ; Match a lambda assigned to a variable
        (declaration
            declarator: (init_declarator
                declarator: (identifier) @name
                value: (lambda_expression) @lambda_node))
    """,
}


class CppTreeSitterParser:
    """A C++-specific parser using tree-sitter, with UE5 macro support."""

    def __init__(self, generic_parser_wrapper):
        self.generic_parser_wrapper = generic_parser_wrapper
        self.language_name = "cpp"
        self.language = generic_parser_wrapper.language
        self.parser = generic_parser_wrapper.parser

    def _get_node_text(self, node) -> str:
        return node.text.decode('utf-8')

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def parse(self, path: Path, is_dependency: bool = False, index_source: bool = False, **kwargs) -> Dict:
        """Parses a C++ file and returns its structure."""
        self.index_source = index_source
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            source_code = f.read()

        # Strip UE5 macros that confuse tree-sitter before parsing
        source_code = _strip_ue5_macros(source_code)

        tree = self.parser.parse(bytes(source_code, "utf8"))
        root_node = tree.root_node

        functions = self._find_functions(root_node)
        functions.extend(self._find_lambda_assignments(root_node))
        function_calls = self._find_calls(root_node)
        classes = self._find_classes(root_node)
        imports = self._find_imports(root_node)
        structs = self._find_structs(root_node)
        enums = self._find_enums(root_node)
        unions = self._find_unions(root_node)
        macros = self._find_macros(root_node)
        variables = self._find_variables(root_node)

        return {
            "path": str(path),
            "functions": functions,
            "classes": classes,
            "structs": structs,
            "enums": enums,
            "unions": unions,
            "macros": macros,
            "variables": variables,
            "declarations": [],
            "imports": imports,
            "function_calls": function_calls,
            "is_dependency": is_dependency,
            "lang": self.language_name,
        }

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------
    def _find_functions(self, root_node):
        functions = []
        query_str = CPP_QUERIES['functions']
        for match in execute_query(self.language, query_str, root_node):
            capture_name = match[1]
            node = match[0]
            if capture_name == 'name':
                # Walk up to function_definition
                func_node = node
                while func_node and func_node.type != 'function_definition':
                    func_node = func_node.parent
                if not func_node:
                    continue

                raw_name = self._get_node_text(node)

                # For qualified_identifier (Class::Method), split into parts
                class_context = None
                if node.type == 'qualified_identifier':
                    scope_node = node.child_by_field_name('scope')
                    name_node = node.child_by_field_name('name')
                    if scope_node and name_node:
                        class_context = self._get_node_text(scope_node)
                        name = self._get_node_text(name_node)
                    else:
                        name = raw_name
                elif node.type == 'destructor_name':
                    name = raw_name
                else:
                    name = raw_name

                params = self._extract_function_params(func_node)

                func_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": func_node.end_point[0] + 1,
                    "args": params,
                }

                if class_context:
                    func_data["full_name"] = raw_name
                    func_data["class_context"] = class_context

                if self.index_source:
                    func_data["source"] = self._get_node_text(func_node)

                functions.append(func_data)
        return functions

    def _extract_function_params(self, func_node) -> list[str]:
        params = []
        declarator_node = func_node.child_by_field_name('declarator')
        if not declarator_node:
            return []

        parameters_node = declarator_node.child_by_field_name('parameters')
        if not parameters_node or parameters_node.type != 'parameter_list':
            return []

        for param in parameters_node.children:
            if param.type == 'parameter_declaration':
                param_decl = param.child_by_field_name('declarator')
                while param_decl and param_decl.type not in ('identifier', 'field_identifier', 'type_identifier'):
                    child = param_decl.child_by_field_name('declarator')
                    if child:
                        param_decl = child
                    else:
                        break

                name = self._get_node_text(param_decl) if param_decl else ""
                param_type_node = param.child_by_field_name('type')
                type_str = self._get_node_text(param_type_node) if param_type_node else ""

                if name:
                    if type_str:
                        params.append(f"{type_str} {name}")
                    else:
                        params.append(name)
        return params

    # ------------------------------------------------------------------
    # Classes  (with base-class extraction)
    # ------------------------------------------------------------------
    def _find_classes(self, root_node):
        classes = []
        query_str = CPP_QUERIES['classes']
        for match in execute_query(self.language, query_str, root_node):
            capture_name = match[1]
            node = match[0]
            if capture_name == 'name':
                class_node = node.parent
                name = self._get_node_text(node)

                # Extract base classes from base_class_clause
                bases = []
                for child in class_node.children:
                    if child.type == 'base_class_clause':
                        for base_child in child.children:
                            if base_child.type == 'type_identifier':
                                bases.append(self._get_node_text(base_child))
                            elif base_child.type == 'qualified_identifier':
                                bases.append(self._get_node_text(base_child))

                class_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": class_node.end_point[0] + 1,
                    "bases": bases,
                }
                if self.index_source:
                    class_data["source"] = self._get_node_text(class_node)
                classes.append(class_data)
        return classes

    # ------------------------------------------------------------------
    # Imports / Includes
    # ------------------------------------------------------------------
    def _find_imports(self, root_node):
        imports = []
        query_str = CPP_QUERIES['imports']
        for match in execute_query(self.language, query_str, root_node):
            capture_name = match[1]
            node = match[0]
            if capture_name == 'path':
                path = self._get_node_text(node).strip('<>').strip('"')
                imports.append({
                    "name": path,
                    "full_import_name": path,
                    "line_number": node.start_point[0] + 1,
                    "alias": None,
                })
        return imports

    # ------------------------------------------------------------------
    # Enums, Structs, Unions, Macros
    # ------------------------------------------------------------------
    def _find_enums(self, root_node):
        enums = []
        query_str = CPP_QUERIES['enums']
        for node, capture_name in execute_query(self.language, query_str, root_node):
            if capture_name == 'name':
                name = self._get_node_text(node)
                enum_node = node.parent
                enum_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": enum_node.end_point[0] + 1,
                }
                if self.index_source:
                    enum_data["source"] = self._get_node_text(enum_node)
                enums.append(enum_data)
        return enums

    def _find_structs(self, root_node):
        structs = []
        query_str = CPP_QUERIES['structs']
        for node, capture_name in execute_query(self.language, query_str, root_node):
            if capture_name == 'name':
                name = self._get_node_text(node)
                struct_node = node.parent
                struct_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": struct_node.end_point[0] + 1,
                }
                if self.index_source:
                    struct_data["source"] = self._get_node_text(struct_node)
                structs.append(struct_data)
        return structs

    def _find_unions(self, root_node):
        unions = []
        query_str = CPP_QUERIES['unions']
        for node, capture_name in execute_query(self.language, query_str, root_node):
            if capture_name == 'name':
                name = self._get_node_text(node)
                union_node = node.parent
                union_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": union_node.end_point[0] + 1,
                }
                if self.index_source:
                    union_data["source"] = self._get_node_text(union_node)
                unions.append(union_data)
        return unions

    def _find_macros(self, root_node):
        macros = []
        query_str = CPP_QUERIES['macros']
        for match in execute_query(self.language, query_str, root_node):
            capture_name = match[1]
            node = match[0]
            if capture_name == 'name':
                macro_node = node.parent
                name = self._get_node_text(node)
                macro_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": macro_node.end_point[0] + 1,
                }
                if self.index_source:
                    macro_data["source"] = self._get_node_text(macro_node)
                macros.append(macro_data)
        return macros

    # ------------------------------------------------------------------
    # Lambdas
    # ------------------------------------------------------------------
    def _find_lambda_assignments(self, root_node):
        functions = []
        query_str = CPP_QUERIES.get('lambda_assignments')
        if not query_str:
            return []

        for match in execute_query(self.language, query_str, root_node):
            capture_name = match[1]
            node = match[0]

            if capture_name == 'name':
                assignment_node = node.parent
                lambda_node = assignment_node.child_by_field_name('value')
                if lambda_node is None or lambda_node.type != 'lambda_expression':
                    continue

                params_node = lambda_node.child_by_field_name('declarator')
                if params_node:
                    params_node = params_node.child_by_field_name('parameters')
                name = self._get_node_text(node)
                params_node = lambda_node.child_by_field_name('parameters')

                context, context_type, _ = self._get_parent_context(assignment_node)
                class_context, _, _ = self._get_parent_context(assignment_node, types=('class_specifier',))

                func_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "end_line": assignment_node.end_point[0] + 1,
                    "args": [p for p in [self._get_node_text(p) for p in params_node.children if p.type == 'identifier'] if p] if params_node else [],
                    "docstring": None,
                    "cyclomatic_complexity": 1,
                    "context": context,
                    "context_type": context_type,
                    "class_context": class_context,
                    "decorators": [],
                    "lang": self.language_name,
                    "is_dependency": False,
                }

                if self.index_source:
                    func_data["source"] = self._get_node_text(assignment_node)

                functions.append(func_data)
        return functions

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------
    def _find_variables(self, root_node):
        variables = []
        query_str = CPP_QUERIES['variables']
        for match in execute_query(self.language, query_str, root_node):
            capture_name = match[1]
            node = match[0]

            if capture_name == 'name':
                assignment_node = node.parent

                right_node = assignment_node.child_by_field_name('value')
                if right_node and right_node.type == 'lambda_expression':
                    continue

                name = self._get_node_text(node)
                value = self._get_node_text(right_node) if right_node else None

                type_node = assignment_node.child_by_field_name('type')
                type_text = self._get_node_text(type_node) if type_node else None

                context, _, _ = self._get_parent_context(node)
                class_context, _, _ = self._get_parent_context(node, types=('class_specifier',))

                variable_data = {
                    "name": name,
                    "line_number": node.start_point[0] + 1,
                    "value": value,
                    "type": type_text,
                    "context": context,
                    "class_context": class_context,
                    "lang": self.language_name,
                    "is_dependency": False,
                }
                variables.append(variable_data)
        return variables

    # ------------------------------------------------------------------
    # Parent context  (handles qualified_identifier for Class::Method)
    # ------------------------------------------------------------------
    def _get_parent_context(self, node, types=('function_definition', 'class_specifier')):
        curr = node.parent
        while curr:
            if curr.type in types:
                if curr.type == 'function_definition':
                    decl = curr.child_by_field_name('declarator')
                    while decl:
                        if decl.type == 'identifier':
                            return self._get_node_text(decl), curr.type, decl.start_point[0] + 1
                        if decl.type == 'qualified_identifier':
                            name_node = decl.child_by_field_name('name')
                            if name_node:
                                return self._get_node_text(name_node), curr.type, decl.start_point[0] + 1
                            return self._get_node_text(decl), curr.type, decl.start_point[0] + 1
                        if decl.type in ('field_identifier', 'destructor_name'):
                            return self._get_node_text(decl), curr.type, decl.start_point[0] + 1

                        child = decl.child_by_field_name('declarator')
                        if child:
                            decl = child
                        else:
                            break
                    return None, curr.type, curr.start_point[0] + 1
                else:
                    name_node = curr.child_by_field_name('name')
                    return self._get_node_text(name_node) if name_node else None, curr.type, curr.start_point[0] + 1
            curr = curr.parent
        return None, None, None

    # ------------------------------------------------------------------
    # Call extraction  (handles arrow/dot, scope-resolution, templates)
    # ------------------------------------------------------------------
    def _find_calls(self, root_node):
        calls = []
        query_str = CPP_QUERIES['calls']
        for node, capture_name in execute_query(self.language, query_str, root_node):
            if capture_name in ("function_name", "method_name", "qualified_call", "template_call"):
                raw_name = self._get_node_text(node)

                if capture_name == "qualified_call":
                    # USFSubsystem::Get  →  name=Get, full=USFSubsystem::Get
                    name_node = node.child_by_field_name('name')
                    func_name = self._get_node_text(name_node) if name_node else raw_name
                    full_name = raw_name
                elif capture_name == "template_call":
                    func_name = raw_name
                    full_name = raw_name
                elif capture_name == "method_name":
                    func_name = raw_name
                    full_name = raw_name
                else:
                    func_name = raw_name
                    full_name = raw_name

                context_name, context_type, context_line = self._get_parent_context(node)
                class_context, _, _ = self._get_parent_context(node, types=("class_specifier",))

                call_data = {
                    "name": func_name,
                    "full_name": full_name,
                    "line_number": node.start_point[0] + 1,
                    "args": [],
                    "inferred_obj_type": None,
                    "context": (context_name, context_type, context_line),
                    "class_context": class_context,
                    "lang": self.language_name,
                    "is_dependency": False,
                }
                calls.append(call_data)
        return calls

    # ------------------------------------------------------------------
    # Fully-qualified name builder
    # ------------------------------------------------------------------
    def _get_full_name(self, node):
        """Builds a fully qualified name for a function or call node."""
        name_parts = []
        curr = node
        while curr:
            if curr.type in ("function_definition", "function_declarator"):
                id_node = curr.child_by_field_name("declarator")
                if id_node and id_node.type == "identifier":
                    name_parts.insert(0, id_node.text.decode("utf8"))
                elif id_node and id_node.type == "qualified_identifier":
                    name_parts.insert(0, id_node.text.decode("utf8"))
            elif curr.type == "class_specifier":
                name_node = curr.child_by_field_name("name")
                if name_node:
                    name_parts.insert(0, name_node.text.decode("utf8"))
            elif curr.type == "namespace_definition":
                name_node = curr.child_by_field_name("name")
                if name_node:
                    name_parts.insert(0, name_node.text.decode("utf8"))
            curr = curr.parent
        return "::".join(name_parts) if name_parts else None


# ======================================================================
# Pre-scan  (builds imports_map used by graph builder for C++ resolution)
# ======================================================================
def pre_scan_cpp(files: list[Path], parser_wrapper) -> dict:
    """
    Quickly scans C++ files to build a map of top-level class, struct, and
    function names to their file paths.  Applies UE5 macro stripping so that
    classes hidden behind *_API macros are correctly discovered.
    """
    imports_map = {}

    query_str = """
        (class_specifier name: (type_identifier) @name)
        (struct_specifier name: (type_identifier) @name)
        (function_definition declarator: (function_declarator declarator: (identifier) @name))
        (function_definition declarator: (function_declarator declarator: (qualified_identifier) @name))
    """

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                source_text = f.read()
            source_text = _strip_ue5_macros(source_text)
            source_bytes = source_text.encode("utf-8")
            tree = parser_wrapper.parser.parse(source_bytes)

            for node, capture_name in execute_query(parser_wrapper.language, query_str, tree.root_node):
                if capture_name == "name":
                    name = node.text.decode("utf-8")
                    # For qualified identifiers (Class::Method) register both
                    # the full name and the short method name.
                    if '::' in name:
                        short_name = name.split('::')[-1]
                        imports_map.setdefault(short_name, []).append(str(path.resolve()))
                    imports_map.setdefault(name, []).append(str(path.resolve()))
        except Exception as e:
            warning_logger(f"Tree-sitter pre-scan failed for {path}: {e}")

    return imports_map
