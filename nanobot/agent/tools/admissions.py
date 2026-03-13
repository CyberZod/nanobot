"""Admissions Demo CRM Tools."""

from typing import Any
from nanobot.agent.tools.base import Tool

# In-memory database for the quick prototype demo
_INQUIRIES = []
_RECEIPTS = []

class RegisterInquiryTool(Tool):
    @property
    def name(self) -> str: 
        return "register_inquiry"
    
    @property
    def description(self) -> str: 
        return "Log a new parent inquiry into the admissions CRM. Use this when a parent asks about admissions."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "parent_name": {"type": "string"},
                "grade_interest": {"type": "string"}
            },
            "required": ["parent_name", "grade_interest"]
        }
    
    async def execute(self, parent_name: str, grade_interest: str, **kwargs) -> str:
        _INQUIRIES.append({"name": parent_name, "grade": grade_interest})
        return f"Successfully logged inquiry for {parent_name} for {grade_interest}. Now tell them the cost and routing details."


class LogReceiptTool(Tool):
    @property
    def name(self) -> str: 
        return "log_receipt"
    
    @property
    def description(self) -> str: 
        return "Save a parent's payment receipt file path to the CRM for review. Use this when a user uploads an image."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "parent_name": {"type": "string"},
                "file_path": {"type": "string", "description": "The path to the image uploaded by the user."}
            },
            "required": ["parent_name", "file_path"]
        }
    
    async def execute(self, parent_name: str, file_path: str, **kwargs) -> str:
        _RECEIPTS.append({"name": parent_name, "file": file_path})
        return f"Successfully saved receipt from {parent_name}. It is awaiting Admin review."


class GetAdmissionsSummaryTool(Tool):
    @property
    def name(self) -> str: 
        return "get_admissions_summary"
    
    @property
    def description(self) -> str: 
        return "Get a summary of today's inquiries and pending receipt paths. Use this when the admin asks for an update. You should send the physical receipt files to the admin using the message tool's media parameter!"
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs) -> str:
        res = ["--- Today's Admissions Report ---"]
        res.append(f"Total New Inquiries: {len(_INQUIRIES)}")
        for iq in _INQUIRIES:
            res.append(f"- {iq['name']} for {iq['grade']}")
        
        res.append(f"\nPending Receipts for Review: {len(_RECEIPTS)}")
        for r in _RECEIPTS:
            res.append(f"- From {r['name']}: {r['file']}")
            
        res.append("\nHint: To show these receipts to the admin, use the `message` tool and add the file path to the `media` array parameter!")
        return "\n".join(res)


class ResetAdmissionsTool(Tool):
    @property
    def name(self) -> str: 
        return "reset_admissions"
    
    @property
    def description(self) -> str: 
        return "Clears all test data from the admissions CRM. Use this when the admin wants to wipe the slate clean."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}
    
    async def execute(self, **kwargs) -> str:
        _INQUIRIES.clear()
        _RECEIPTS.clear()
        return "Admissions test data has been successfully wiped clean."
