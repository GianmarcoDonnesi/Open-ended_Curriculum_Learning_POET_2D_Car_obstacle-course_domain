import pygame
import random
import numpy as np
from Box2D.b2 import world, polygonShape, circleShape, revoluteJointDef
from Box2D.b2 import rayCastCallback as b2RayCastCallback
import gymnasium as gym
from gymnasium import spaces
from collections import deque

PPM = 20.0
FPS = 60
STEP_T = 1.0 / FPS
SCREEN_W, SCREEN_H = 800, 600
GREEN = (0, 255, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)

class RayCastCallback(b2RayCastCallback):
    def __init__(self):
        super().__init__()
        self.hit = False
        self.fraction = 1.0
    
    def ReportFixture(self, fixture, point, normal, fraction):
        self.hit = True
        self.fraction = fraction
        return fraction

class CarEnvironment(gym.Env):
    metadata = {'render.modes': ['human']}
    pygame_initialized = False

    def __init__(self):
        super(CarEnvironment, self).__init__()

        if not CarEnvironment.pygame_initialized:
            pygame.init()
            CarEnvironment.pygame_initialized = True

        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption("POET 2D Car Simulation")
        self.clock = pygame.time.Clock()
        self.world = world(gravity=(0, -17), doSleep=True) #Increased gravity for car stability
        self.bodies = []
        self.obstacles = []
        self.obstacles_config = [
            {"base_position": (20, 1), "size": (1, 0.7), "color": BLACK, "obstacle_type": 'ramp'},
            {"base_position": (25, 1), "size": (1, 1), "color": BLACK, "obstacle_type": 'hole'},
            {"base_position": (30, 1), "size": (2, 1), "color": BLACK, "obstacle_type": 'bump'}
        ]
        self.step_count = 0
        self.max_steps = 1000
        self.passed_obstacles = set()
        self.prev_positions = deque(maxlen=30)
        self.car = None
        self.camera = (0, 0)

        self.action_space = spaces.Discrete(9) 
        self.observation_space = spaces.Box(low=-np.inf,
                                            high=np.inf,
                                            shape=(4 + 5,),  
                                            dtype=np.float32)
        
        try:
            self.background_texture = pygame.image.load("textures/background.png").convert()
            self.background_texture = pygame.transform.scale(self.background_texture, (SCREEN_W, SCREEN_H))
        except pygame.error as e:
            print(f"Error background.png: {e}")
            self.background_texture = pygame.Surface((SCREEN_W, SCREEN_H))
            self.background_texture.fill(WHITE)

        try:
            self.wheel_texture = pygame.image.load("textures/wheel.png").convert_alpha()
            wheel_radius = 0.6 
            self.wheel_texture = pygame.transform.scale(self.wheel_texture, (int(wheel_radius * 2 * PPM), int(wheel_radius * 2 * PPM)))
        except pygame.error as e:
            print(f"Error wheel.png: {e}")
            wheel_radius = 0.6
            self.wheel_texture = pygame.Surface((int(wheel_radius * 2 * PPM), int(wheel_radius * 2 * PPM)), pygame.SRCALPHA)
            pygame.draw.circle(self.wheel_texture, (100, 100, 100), (int(wheel_radius * PPM), int(wheel_radius * PPM)), int(wheel_radius * PPM))
        
        self.actions = [
            (-1, -1),  # 0: Steer left, reverse
            (-1, 0),   # 1: Steer left, no acceleration
            (-1, 1),   # 2: Steer left, forward
            (0, -1),   # 3: No steering, reverse
            (0, 0),    # 4: No steering, no acceleration
            (0, 1),    # 5: No steering, forward
            (1, -1),   # 6: Steer right, reverse
            (1, 0),    # 7: Steer right, no acceleration
            (1, 1)     # 8: Steer right, forward
        ]

        self.done = False
        self.reset()

    def reset(self):
        self.world = world(gravity=(0, -17), doSleep=True)
        self.bodies = []
        self.passed_obstacles = set()
        self.prev_positions = deque(maxlen=30)
        self.done = False
        self.step_count = 0
        
        self.car = Car(self.world, position=(10, 2.2))
        self.add_body(self.car.body, BLACK)
        self.add_body(self.car.wheel_front, BLACK)
        self.add_body(self.car.wheel_rear, BLACK)
        
        self.camera = (0, 0)
        
        self.obstacles = []
        for obs_params in self.obstacles_config:
            self.modify_env(obs_params)

        self._create_ground_with_holes()
        
        return self._get_state(), {}
    
    def _create_ground_with_holes(self):
        ground_length = SCREEN_W / PPM * 40
        segment_length = 1
        hole_intervals = []
        for obs in self.obstacles:
            if obs['type'] == 'hole':
                hole_x, hole_width = obs['params']['base_position'][0], obs['params']['size'][0]
                hole_intervals.append((hole_x, hole_x + hole_width))

        for i in range(int(ground_length / segment_length)):
            x = i * segment_length + 0.5 * segment_length
            is_hole = any(start <= x <= end for (start, end) in hole_intervals)

            if not is_hole:
                ground_segment = self.world.CreateStaticBody(
                    position=(x, 0),
                    shapes=polygonShape(box=(segment_length / 2, 1))
                )
                self.add_body(ground_segment, GREEN)
    
    def _is_overlapping_with_existing_obstacles(self, x, y, width, height):
        for obs in self.obstacles:
            obs_x, obs_y = obs['params']['base_position']
            obs_w, obs_h = obs['params']['size']
            if (x < obs_x + obs_w) and (obs_x < x + width):
                return True
        return False


    def step(self, action):
        steering, acceleration = self.actions[action]
        self.apply_discrete_action(steering, acceleration)
        self.step_count += 1
        self.world.Step(STEP_T, 6, 2)
        self.world.ClearForces()
        self._update_camera()
        self.prev_positions.append(self.car.body.position.copy())
        self.done = self._check_done()
        reward = self._calculate_reward()
        state = self._get_state()

        if self._should_add_obstacle():
            self._add_new_obstacle()
        
        if self.step_count > self.max_steps:
            self.done = True

        return state, reward, self.done, {}

    def apply_discrete_action(self, steering, acceleration):
        motor_speed = float(acceleration * 45)
        steering_speed = 7.0
        self.car.joint_front.motorSpeed = motor_speed + steering * steering_speed
        self.car.joint_rear.motorSpeed = motor_speed - steering * steering_speed

    def render(self, mode='human'):
        self.screen.blit(self.background_texture, (0,0))
        for body, color in self.bodies:
            for fixture in body.fixtures:
                shape = fixture.shape
                if isinstance(shape, polygonShape):
                    vertices = [body.transform * v * PPM for v in shape.vertices]
                    vertices = [(v[0] - self.camera[0],
                                SCREEN_H - v[1] - self.camera[1]) for v in vertices]
                    pygame.draw.polygon(self.screen, color, vertices)
                elif isinstance(shape, circleShape):
                    wheel_pos = body.position
                    wheel_angle = body.angle
                    rotated_wheel = pygame.transform.rotate(self.wheel_texture, -wheel_angle * 180 / np.pi)
                    wheel_x = wheel_pos[0] * PPM - self.camera[0] - rotated_wheel.get_width() / 2
                    wheel_y = SCREEN_H - (wheel_pos[1] * PPM) - self.camera[1] - rotated_wheel.get_height() / 2
                    self.screen.blit(rotated_wheel, (wheel_x, wheel_y))
                    
        pygame.display.flip()
        self.clock.tick(FPS)

    def close(self):
        pygame.quit()

    def add_body(self, body, color):
        self.bodies.append((body, color))

    def modify_env(self, params):
        x, y = params["base_position"]
        width, height = params["size"]
        obstacle_type = params["obstacle_type"]
        color = params["color"]

        if obstacle_type == "ramp":
            vertices = [(0, 0), (width, 0), (width, height)]
            ramp = self.world.CreateStaticBody(
                position=(x, y),
                shapes=polygonShape(vertices=vertices)
            )
            self.add_body(ramp, color)
            self.obstacles.append({
                'body': ramp,
                'type': obstacle_type,
                'params': params
            })

        elif obstacle_type == "hole":
            width = min(width, 3.0)
            height = 1
            if self._is_overlapping_with_existing_obstacles(x, y, width, height):
                return
            self.obstacles.append({
                'body': None,
                'type': obstacle_type,
                'params': {"base_position": (x, y), "size": (width, height), "color": color, "obstacle_type": obstacle_type}
            })

        elif obstacle_type == "bump":
            vertices = [(0, 0), (width, 0), (width / 2, height)]
            bump = self.world.CreateStaticBody(
                position=(x, y),
                shapes=polygonShape(vertices=vertices)
            )
            self.add_body(bump, color)
            self.obstacles.append({
                'body': bump,
                'type': obstacle_type,
                'params': params
            })

        else:
            raise ValueError("Unsupported obstacle type")
        
    def clone(self):
        new_env = CarEnvironment.__new__(CarEnvironment)
        CarEnvironment.__init__(new_env)
        new_env.obstacles_config = [dict(p) for p in self.obstacles_config]
        new_env.reset()

        new_env.car = Car(new_env.world, position=self.car.body.position)
        new_env.add_body(new_env.car.body, BLACK)
        new_env.add_body(new_env.car.wheel_front, BLACK)
        new_env.add_body(new_env.car.wheel_rear, BLACK)

        new_env.step_count = self.step_count
        new_env.done = self.done
        new_env.camera = self.camera
        new_env.prev_positions = deque(self.prev_positions, maxlen=30)
        new_env.passed_obstacles = set(self.passed_obstacles)
        return new_env

    def _get_state(self):
        car_pos = self.car.body.position
        car_vel = self.car.body.linearVelocity
        # Get LiDAR Data
        lidar_data = self.car.get_lidar_data(num_sensors=5, max_distance=20, angle_range=(-np.pi/6, np.pi/6))
        return np.concatenate(([car_pos[0], car_pos[1], car_vel[0], car_vel[1]], lidar_data))

    def _update_camera(self):
        car_x = self.car.body.position[0] * PPM
        car_y = self.car.body.position[1] * PPM
        x = car_x - SCREEN_W / 2
        y = car_y - SCREEN_H / 2
        x = max(0, x)
        y = max(0, y)
        self.camera = (x, y)

    def _check_done(self):
        car_x, car_y = self.car.body.position
        for idx, obs in enumerate(self.obstacles):
            if obs['type'] == "hole":
                base_pos = obs['params']['base_position']
                size = obs['params']['size']
                hole_x, hole_width = base_pos[0], size[0]

                if hole_x <= car_x <= hole_x + hole_width and car_y < 1:
                    self.done_reason = 'hole'
                    return True 

        self.done_reason = 'fell'
        return car_y < 0 

    def _calculate_reward(self):
        reward = 0
        #1. Reward for forward movement
        if len(self.prev_positions) >= 2:
            current_x = self.prev_positions[-1][0]
            previous_x = self.prev_positions[-2][0]
            delta_x = current_x - previous_x
            reward += delta_x * 10 

            #2. Penalties for backward movements
            if delta_x < 0:
                reward -= 100
        else:
            delta_x = 0 

        #3. Reward for each step
        reward += 1

        #4. Specific rewards for overcoming obstacles
        for idx, obs in enumerate(self.obstacles):
            base_x, base_y = obs['params']['base_position']
            size_x, size_y = obs['params']['size']
            if base_x + size_x < self.car.body.position[0] and idx not in self.passed_obstacles:
                if obs['type'] == 'ramp':
                    reward += 300 
                elif obs['type'] == 'hole':
                    reward += 200
                elif obs['type'] == 'bump':
                    reward += 180  
                self.passed_obstacles.add(idx)

        #Penalty for not moving enough
        if len(self.prev_positions) == self.prev_positions.maxlen:
            positions = np.array([[pos[0], pos[1]] for pos in self.prev_positions])
            movement = positions.max(axis=0) - positions.min(axis=0)
            movement_threshold = 0.5 
            if np.all(movement < movement_threshold):
                reward -= 20 

        #5. Incentivize stability and penalize excessive speeds
        if len(self.prev_positions) >= 2:
            velocity = self.car.body.linearVelocity[0]
            reward += velocity * 0.5  
            reward -= 0.1 * velocity**2 

        #6. Penalty for fall or end of episode
        if self.done:
            if hasattr(self, 'done_reason') and self.done_reason == 'hole':
                reward -= 50  
            else:
                reward -= 100  

        return reward

    def _should_add_obstacle(self):
        if not self.obstacles:
            return False
        last_obs = self.obstacles[-1]
        base_pos = last_obs['params']['base_position']
        size = last_obs['params']['size']
        last_pos_x = base_pos[0] + size[0]
        car_x = self.car.body.position[0]
        return car_x > last_pos_x - 20

    def _add_new_obstacle(self):
        last_obs = self.obstacles[-1]
        base_pos = last_obs['params']['base_position']
        size = last_obs['params']['size']
        last_pos_x = base_pos[0] + size[0]
        new_obstacle_x = last_pos_x + random.uniform(5, 10)
        new_obstacle_y = 1

        obstacle_type = random.choice(["ramp", "hole", "bump"])
        if obstacle_type == "ramp":
            size = (random.uniform(2, 10), random.uniform(2, 5))
        elif obstacle_type == "hole":
            size = (random.uniform(0.5, 1.5), 0.5)
            if self._is_overlapping_with_existing_obstacles(new_obstacle_x, new_obstacle_y, size[0], size[1]):
                return
        elif obstacle_type == "bump":
            size = (random.uniform(1, 4), random.uniform(0.5, 1.5))

        self.modify_env({
            "base_position":(new_obstacle_x, new_obstacle_y),
            "size": size,
            "color": BLACK,
            "obstacle_type": obstacle_type
        })
        

    def evaluate_agent(self, ddqn_agent, theta, verbose=True):
        if theta is not None:
            ddqn_agent.network.network.load_state_dict(theta)
        return ddqn_agent.evaluate(self, ep_n=1, render=False, verbose=verbose)

class Car:
    def __init__(self, world, position=(10, 2.2)):
        self.world = world
        self._create_car(position)

    def _create_car(self, position):
        car_width, car_height = 3.5, 1.4
        self.body = self.world.CreateDynamicBody(position=position)
        self.body.CreatePolygonFixture(box=(car_width / 2, car_height / 2),
                                    density=2.5, friction=0.5)
        
        wheel_radius = 0.6
        wheel_distance = 1.2  
        wheel_offset_y = - (car_height / 2 + wheel_radius)
        
        wheel_front_pos = (position[0] + wheel_distance, position[1] + wheel_offset_y)
        wheel_rear_pos = (position[0] - wheel_distance, position[1] + wheel_offset_y)
        self.wheel_front = self._create_wheel(wheel_front_pos, wheel_radius)
        self.wheel_rear = self._create_wheel(wheel_rear_pos, wheel_radius)
        
        self.joint_front = self._create_wheel_joint(self.body, self.wheel_front, wheel_front_pos)
        self.joint_rear = self._create_wheel_joint(self.body, self.wheel_rear, wheel_rear_pos)

    def _create_wheel(self, position, radius=0.5):
        wheel = self.world.CreateDynamicBody(position=position)
        wheel.CreateCircleFixture(radius=radius, density=1.5, friction=1.8)
        return wheel

    def _create_wheel_joint(self, bodyA, bodyB, anchor):
        joint_def = revoluteJointDef(
            bodyA=bodyA,
            bodyB=bodyB,
            anchor=anchor,
            enableMotor=True,
            maxMotorTorque=700,
            motorSpeed=0.7
        )
        joint = self.world.CreateJoint(joint_def)
        return joint
    
    def get_lidar_data(self, num_sensors, max_distance, angle_range):
        start_angle, end_angle = angle_range
        angles = np.linspace(start_angle, end_angle, num_sensors)
        lidar_data = []
        for angle in angles:
            ray_direction = (np.cos(angle), np.sin(angle))
            endpoint = (
                self.body.position[0] + max_distance * ray_direction[0],
                self.body.position[1] + max_distance * ray_direction[1],
            )
            callback = RayCastCallback()
            self.world.RayCast(callback, self.body.position, endpoint)
            if callback.hit:
                distance = callback.fraction * max_distance
            else:
                distance = max_distance
            lidar_data.append(distance)
        return np.array(lidar_data)
    
    def apply_action(self, steering, acceleration):
        motor_speed = float(acceleration * 45)
        steering_speed = 7.0
        self.joint_front.motorSpeed = motor_speed + steering * steering_speed
        self.joint_rear.motorSpeed = motor_speed - steering * steering_speed