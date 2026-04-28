"""
工具输入验证模块

本模块负责工具输入的验证，使用 Pydantic 实现。
"""

from typing import Any, Dict, Optional, List, Tuple
from pydantic import BaseModel, ValidationError

class ValidationResult(BaseModel):
    """
    验证结果

    [Workflow]
    验证成功：result=True
    验证失败：result=False, message=错误信息, error_code=错误码
    """
    result: bool
    message: Optional[str] = None
    error_code: Optional[int] = None

def validate_tool_input(
    tool_name: str,
    input_schema: type[BaseModel],
    input_data: Dict[str, Any]
) -> Tuple[bool, Optional[BaseModel], Optional[str]]:
    """
    验证工具输入

    [Workflow]
    1. 使用 Pydantic schema 验证输入
    2. 如果验证成功，返回解析后的数据
    3. 如果验证失败，格式化错误信息

    Args:
        tool_name: 工具名称
        input_schema: Pydantic 输入 schema
        input_data: 输入数据（字典）

    Returns:
        Tuple[bool, Optional[BaseModel], Optional[str]]:
            - success: 是否验证成功
            - parsed_data: 解析后的数据（成功时）
            - error_message: 错误信息（失败时）

    Examples:
        >>> from pydantic import BaseModel
        >>> class BashInput(BaseModel):
        ...     command: str
        ...     timeout: int = 120000
        >>> success, data, error = validate_tool_input(
        ...     "bash",
        ...     BashInput,
        ...     {"command": "ls -la"}
        ... )
        >>> success
        True
    """
    try:
        # 步骤 1: 使用 Pydantic 验证输入

        parsed_data = input_schema(**input_data)

        # 步骤 2: 验证成功，返回解析后的数据
        return True, parsed_data, None

    except ValidationError as e:
        # 步骤 3: 验证失败，格式化错误信息

        error_message = format_pydantic_validation_error(tool_name, e)
        return False, None, error_message

def format_pydantic_validation_error(
    tool_name: str,
    error: ValidationError
) -> str:
    """
    格式化 Pydantic 验证错误

    [Workflow]
    1. 解析 Pydantic 错误
    2. 分类错误类型：
       - 缺失参数 (missing)
       - 意外参数 (extra)
       - 类型不匹配 (type_error)
    3. 格式化为用户友好的错误信息

    Args:
        tool_name: 工具名称
        error: Pydantic ValidationError

    Returns:
        str: 格式化的错误信息

    Examples:
        >>> error_msg = format_pydantic_validation_error("bash", validation_error)
        >>> print(error_msg)
        bash failed due to the following issue:
        - The required parameter `command` is missing
    """
    # 步骤 1: 解析错误
    errors = error.errors()

    # 步骤 2: 分类错误
    missing_params = []
    extra_params = []
    type_errors = []

    for err in errors:
        error_type = err['type']
        field = '.'.join(str(loc) for loc in err['loc'])

        if error_type == 'missing':
            # 缺失参数
            missing_params.append(field)
        elif error_type == 'extra_forbidden':
            # 意外参数
            extra_params.append(field)
        else:
            # 类型不匹配或其他错误
            msg = err.get('msg', 'Invalid value')
            type_errors.append(f"{field}: {msg}")

    # 步骤 3: 格式化错误信息
    issue_count = len(missing_params) + len(extra_params) + len(type_errors)
    issue_word = "issue" if issue_count == 1 else "issues"

    lines = [f"{tool_name} failed due to the following {issue_word}:"]

    # 添加缺失参数错误
    for param in missing_params:
        lines.append(f"- The required parameter `{param}` is missing")

    # 添加意外参数错误
    for param in extra_params:
        lines.append(f"- An unexpected parameter `{param}` was provided")

    # 添加类型错误
    for error_msg in type_errors:
        lines.append(f"- {error_msg}")

    return '\n'.join(lines)

def check_required_fields(
    input_schema: type[BaseModel],
    input_data: Dict[str, Any]
) -> List[str]:
    """
    检查必需字段

    [Workflow]
    1. 获取 schema 的必需字段
    2. 检查输入数据中是否包含所有必需字段
    3. 返回缺失的字段列表

    Args:
        input_schema: Pydantic 输入 schema
        input_data: 输入数据

    Returns:
        List[str]: 缺失的必需字段列表
    """
    # 获取必需字段
    required_fields = []
    for field_name, field_info in input_schema.model_fields.items():
        if field_info.is_required():
            required_fields.append(field_name)

    # 检查缺失的字段
    missing_fields = []
    for field in required_fields:
        if field not in input_data:
            missing_fields.append(field)

    return missing_fields

def check_extra_fields(
    input_schema: type[BaseModel],
    input_data: Dict[str, Any]
) -> List[str]:
    """
    检查额外字段

    [Workflow]
    1. 获取 schema 定义的所有字段
    2. 检查输入数据中是否有未定义的字段
    3. 返回额外字段列表

    Args:
        input_schema: Pydantic 输入 schema
        input_data: 输入数据

    Returns:
        List[str]: 额外字段列表
    """
    # 获取定义的字段
    defined_fields = set(input_schema.model_fields.keys())

    # 检查额外字段
    extra_fields = []
    for field in input_data.keys():
        if field not in defined_fields:
            extra_fields.append(field)

    return extra_fields

def validate_field_type(
    field_name: str,
    field_value: Any,
    expected_type: type
) -> Tuple[bool, Optional[str]]:
    """
    验证字段类型

    [Workflow]
    1. 检查字段值的类型是否匹配预期类型
    2. 返回验证结果和错误信息

    Args:
        field_name: 字段名称
        field_value: 字段值
        expected_type: 预期类型

    Returns:
        Tuple[bool, Optional[str]]:
            - is_valid: 是否有效
            - error_message: 错误信息（无效时）
    """
    if not isinstance(field_value, expected_type):
        actual_type = type(field_value).__name__
        expected_type_name = expected_type.__name__
        error_msg = (
            f"The parameter `{field_name}` type is expected as `{expected_type_name}` "
            f"but provided as `{actual_type}`"
        )
        return False, error_msg

    return True, None
