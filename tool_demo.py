import asyncio
from typing import Any
from nanobot.agent.tools.base import Tool

# 1. We create a concrete Tool by inheriting from the Blueprint (Tool)
class WeatherTool(Tool):
    @property
    def name(self) -> str:
        return "get_weather"

    @property
    def description(self) -> str:
        return "Get weather for a city"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "city": {"type": "string", "minLength": 3},
                "units": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["city"]
        }

    async def execute(self, **kwargs: Any) -> str:
        return f"It's sunny in {kwargs['city']}!"

# 2. Let's test the "Security Guard" (validate_params)
async def main():
    tool = WeatherTool()
    
    # Example A: Perfect parameters
    print("--- Example A: Valid Data ---")
    data_ok = {"city": "New York", "units": "celsius"}
    errors_ok = tool.validate_params(data_ok)
    print(f"Input: {data_ok} -> Errors: {errors_ok}")

    # Example B: Missing mandatory field
    print("\n--- Example B: Missing 'city' ---")
    data_bad1 = {"units": "celsius"}
    errors_bad1 = tool.validate_params(data_bad1)
    print(f"Input: {data_bad1} -> Errors: {errors_bad1}")

    # Example C: Too short city name and wrong enum
    print("\n--- Example C: Multiple Errors ---")
    data_bad2 = {"city": "NY", "units": "kelvin"} # 'NY' is too short, 'kelvin' not in enum
    errors_bad2 = tool.validate_params(data_bad2)
    print(f"Input: {data_bad2} -> Errors: {errors_bad2}")

if __name__ == "__main__":
    asyncio.run(main())
