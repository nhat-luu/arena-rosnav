from typing import Any, Dict, List, Tuple

import numpy as np
import rospy

from .constants import REWARD_CONSTANTS
from .utils import load_rew_fnc, min_distance_from_pointcloud


class RewardFunction:
    """Represents a reward function for a reinforcement learning environment.

    Attributes:
        _rew_func_name (str): Name of the yaml file that contains the reward function specifications.
        _robot_radius (float): Radius of the robot.
        _safe_dist (float): Safe distance of the agent.
        _goal_radius (float): Radius of the goal.

        _internal_state_info (Dict[str, Any]): Centralized internal state info for the reward units.
            E.g. to avoid computing same parameter in a single step multiple times.

        _curr_reward (float): Current reward value.
        _info (Dict[str, Any]): Dictionary containing reward function information.

        _rew_fnc_dict (Dict[str, Dict[str, Any]]): Dictionary containing reward function specifications.
        _reward_units (List[RewardUnit]): List of reward units for calculating the reward.
    """

    _rew_func_name: str
    _robot_radius: float
    _safe_dist: float
    _goal_radius: float

    _internal_state_info: Dict[str, Any]

    _curr_reward: float
    _info: Dict[str, Any]

    _rew_fnc_dict: Dict[str, Dict[str, Any]]
    _reward_units: List["RewardUnit"]

    def __init__(
        self,
        rew_func_name: str,
        robot_radius: float,
        goal_radius: float,
        safe_dist: float,
        *args,
        **kwargs,
    ):
        """This class represents a reward function for a reinforcement learning environment.

        Args:
            rew_func_name (str): Name of the yaml file that contains the reward function specifications.
            robot_radius (float): Radius of the robot.
            goal_radius (float): Radius of the goal.
            safe_dist (float): Safe distance of the agent.
        """
        self._rew_func_name = rew_func_name
        self._robot_radius = robot_radius
        self._safe_dist = safe_dist
        self._goal_radius = goal_radius

        # globally accessible and required information for RewardUnits
        self._internal_state_info: Dict[str, Any] = {}

        self._curr_reward = 0
        self._info = {}

        self._rew_fnc_dict = load_rew_fnc(self._rew_func_name)
        self._reward_units: List["RewardUnit"] = self._setup_reward_function()

    def _setup_reward_function(self) -> List["RewardUnit"]:
        """Sets up the reward function.

        Returns:
            List[RewardUnit]: List of reward units for calculating the reward.
        """
        import rl_utils.utils.rewards as rew_pkg

        return [
            rew_pkg.RewardUnitFactory.instantiate(unit_name)(
                reward_function=self, **kwargs
            )
            for unit_name, kwargs in self._rew_fnc_dict.items()
        ]

    def add_reward(self, value: float):
        """Adds the specified value to the current reward.

        Args:
            value (float): Reward to be added. Typically called by the RewardUnit.
        """
        self._curr_reward += value

    def add_info(self, info: Dict[str, Any]):
        """Adds the specified information to the reward function's info dictionary.

        Args:
            info (Dict[str, Any]): RewardUnits information to be added.
        """
        self._info.update(info)

    def add_internal_state_info(self, key: str, value: Any):
        """Adds internal state information to the reward function.

        Args:
            key (str): Key for the internal state information.
            value (Any): Value of the internal state information.
        """
        self._internal_state_info[key] = value

    def get_internal_state_info(self, key: str, default: Any = None) -> Any:
        """Retrieves internal state information based on the specified key.

        Args:
            key (str): Key for the internal state information.

        Returns:
            Any: Value of the internal state information.
        """
        if self._internal_state_info[key]:
            return self._internal_state_info[key]
        else:
            return default

    def update_internal_state_info(
        self,
        laser_scan: np.ndarray = None,
        point_cloud: np.ndarray = None,
        from_aggregate_obs: bool = False,
        *args,
        **kwargs,
    ):
        """Updates the internal state info after each time step.

        The internal state dicitonary saves information globally accessible for every RewardUnit.
        It is intended for information which needs to be calculated only once.

        Args:
            laser_scan (np.ndarray, optional): Array containing the laser data. Defaults to None.
            point_cloud (np.ndarray, optional): Array containing the point cloud data. Defaults to None.
            from_aggregate_obs (bool, optional):  Iff the observation from the aggreation (GetDump.srv) should be considered.
                Defaults to False.
        """
        self.set_min_dist_laser(laser_scan, point_cloud, from_aggregate_obs)
        self.set_safe_dist_breached()

    def reset_internal_state_info(self):
        """Resets all global state information (after each environment step)."""
        for key in self._internal_state_info.keys():
            self._internal_state_info[key] = None

    def _reset(self):
        """Reset on every environment step."""
        self._curr_reward = 0
        self._info = {}
        self.reset_internal_state_info()

    def reset(self):
        """Reset before each episode."""
        self.goal_radius = rospy.get_param("/goal_radius", 0.3)

        for reward_unit in self._reward_units:
            reward_unit.reset()

    def calculate_reward(self, laser_scan: np.ndarray, *args, **kwargs) -> None:
        """Calculates the reward based on several observations.

        Args:
            laser_scan (np.ndarray): Array containing the laser data.
        """
        for reward_unit in self._reward_units:
            if self.safe_dist_breached and not reward_unit.on_safe_dist_violation:
                continue
            reward_unit(laser_scan=laser_scan, **kwargs)

    def get_reward(
        self,
        laser_scan: np.ndarray = None,
        point_cloud: np.ndarray = None,
        from_aggregate_obs: bool = False,
        *args,
        **kwargs,
    ) -> Tuple[float, Dict[str, Any]]:
        """Retrieves the current reward and info dictionary.

        Args:
            laser_scan (np.ndarray): Array containing the laser data.
            point_cloud (np.ndarray): Array containing the point cloud data.
            from_aggregate_obs (bool): Iff the observation from the aggreation (GetDump.srv) should be considered.

        Returns:
            Tuple[float, Dict[str, Any]]: Tuple of the current timesteps reward and info.
        """
        self._reset()
        self.update_internal_state_info(
            laser_scan=laser_scan,
            point_cloud=point_cloud,
            from_aggregate_obs=from_aggregate_obs,
            **kwargs,
        )
        self.calculate_reward(**kwargs)
        return self._curr_reward, self._info

    @property
    def robot_radius(self) -> float:
        return self._robot_radius

    @property
    def goal_radius(self) -> float:
        return self._goal_radius

    @goal_radius.setter
    def goal_radius(self, value) -> None:
        if value < REWARD_CONSTANTS.MIN_GOAL_RADIUS:
            raise ValueError(
                f"Goal radius smaller than {REWARD_CONSTANTS.MIN_GOAL_RADIUS}"
            )
        self._goal_radius = value

    @property
    def safe_dist_breached(self) -> bool:
        return self._safe_dist_breached

    def set_min_dist_laser(
        self, laser_scan: np.ndarray, point_cloud: np.ndarray, from_aggregate_obs: bool
    ):
        if not laser_scan and not point_cloud:
            raise ValueError("Neither LaserScan nor PointCloud data was provided!")

        if not from_aggregate_obs:
            self.add_internal_state_info("min_dist_laser", laser_scan.min())
        else:
            self.add_internal_state_info(
                "min_dist_laser", min_distance_from_pointcloud(point_cloud)
            )

    def set_safe_dist_breached(self) -> None:
        self.add_internal_state_info(
            "safe_dist_breached",
            self.get_internal_state_info("min_dist_laser") <= self._safe_dist,
        )

    def __repr__(self) -> str:
        format_string = self.__class__.__name__ + "("
        for name, params in self._rew_fnc_dict.items():
            format_string += "\n"
            format_string += f"{name}: {params}"
        format_string += "\n)"
        return format_string
