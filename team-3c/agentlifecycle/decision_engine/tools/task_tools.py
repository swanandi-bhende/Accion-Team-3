class TaskToolInterface:
    async def get_task(self, task_id: str):
        raise NotImplementedError
