# -*- coding: utf-8 -*-
"""donkey_car_qnn.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1ZQNxiEaJQk5fZb75LkUpI3hu7z2Dyniw
"""

import gym
import torch.optim.lr_scheduler as lr_scheduler
import argparse
import gym_donkeycar
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import pennylane as qml
import itertools
from torch.nn.parameter import Parameter
import torch.nn.functional as F
import time


# Argument parsing setup
parser = argparse.ArgumentParser(description="Quantum Neural Network for Donkey Car")
parser.add_argument("--env_name", type=str, default="donkey-mountain-track-v0", help="Donkey Car environment name")
parser.add_argument("--port", type=int, default=9091, help="Port to use for connecting to the simulator")
parser.add_argument("--episodes", type=int, default=4000, help="Number of episodes to train")
parser.add_argument("--learning_rate", type=float, default=0.05, help="Learning rate for the optimizer")
parser.add_argument("--gamma", type=float, default=0.75, help="Discount factor for Q-learning")
parser.add_argument("--n_qubits", type=int, default=15  , help="Number of qubits in the quantum circuit")
parser.add_argument("--save_model", type=str, default="qnn_donkeycar.pth", help="File path to save the trained model")
args = parser.parse_args()

def get_quantum_device(n_qubits):
    return qml.device('default.qubit', wires=n_qubits)

class QuantumLayer(nn.Module):
    def __init__(self, n_qubits, output_dim):
        super(QuantumLayer, self).__init__()
        self.n_qubits = n_qubits
        self.qnode = qml.QNode(self.quantum_circuit, get_quantum_device(n_qubits), interface='torch')
        self.weights = Parameter(torch.randn((1, n_qubits, 3)))  # Adjust dimensions as needed
        self.output_dim = output_dim

    def quantum_circuit(self, inputs, weights):
        qml.templates.StronglyEntanglingLayers(weights, wires=range(self.n_qubits))
        # Returning expectation values for each qubit as an example
        return [qml.expval(qml.PauliZ(wires=i)) for i in range(self.output_dim)]

    def forward(self, inputs):
        # Ensure inputs is a flattened 1D tensor if necessary
        q_outputs = self.qnode(inputs, self.weights)
        # Process q_outputs as needed to match your network architecture
        return torch.stack(q_outputs).float()

class QuantumActor(nn.Module):
    def __init__(self, input_dim, n_qubits, output_dim):
        super(QuantumActor, self).__init__()
        self.fc1 = nn.Linear(input_dim, n_qubits)
        self.relu = nn.ReLU()
        self.q_layer = QuantumLayer(n_qubits, output_dim)  # Your quantum layer adapted for actor output
        self.fc2 = nn.Linear(output_dim, output_dim)  # Final layer to match the action dimension

    def forward(self, x):
        x = self.fc1(x)
        x = self.relu(x)
        x = self.q_layer(x)
        x = self.fc2(x)  # Ensure this matches the expected action space dimensions
        return x

class Critic(nn.Module):
    """Critic Network."""
    def __init__(self, state_dim, action_dim):
        super(Critic, self).__init__()
        self.fc1 = nn.Linear(state_dim + action_dim, 256)
        self.fc2 = nn.Linear(256, 128)
        self.fc3 = nn.Linear(128, 1)

    def forward(self, state, action):
        x = torch.cat([state, action], dim=1)
        x = nn.ReLU()(self.fc1(x))
        x = nn.ReLU()(self.fc2(x))
        return self.fc3(x)

class DDPGAgent:
    def __init__(self, state_dim, action_dim, learning_rate=0.05, epsilon=1.0, epsilon_min=0.01):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.recent_rewards = []  # Store recent episode rewards for performance evaluation

        # Actor Network
        self.actor = QuantumActor(input_dim=state_dim, n_qubits=args.n_qubits, output_dim=action_dim)
        self.actor_optimizer = optim.Adam(self.actor.parameters(), lr=learning_rate)
        self.actor_scheduler = lr_scheduler.StepLR(self.actor_optimizer, step_size=200, gamma=0.95)

        # Critic Network
        self.critic = Critic(state_dim, action_dim)
        self.critic_optimizer = optim.Adam(self.critic.parameters(), lr=learning_rate * 0.5)
        self.critic_scheduler = lr_scheduler.StepLR(self.critic_optimizer, step_size=200, gamma=0.95)

    def select_action(self, state, noise_scale=0.1):
        if np.random.rand() <= self.epsilon:
            # Take a random action
            return np.random.uniform(-1, 1, size=self.action_dim)

        state = torch.as_tensor(state, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            action = self.actor(state).squeeze(0).cpu().numpy()

        # Add noise for exploration
        noise = noise_scale * np.random.randn(self.action_dim)
        action += noise
        action[1] = np.abs(action[1])  # Ensure car doesn't drive backward

        return np.clip(action, -1, 1)

    def adjust_epsilon_based_on_performance(self, improvement_threshold=0.01, exploration_increase=0.05, exploration_decrease=0.02):
        if len(self.recent_rewards) >= 20:
            recent_performance = sum(self.recent_rewards[-10:]) / 10
            past_performance = sum(self.recent_rewards[-20:-10]) / 10
            performance_improvement = recent_performance - past_performance
            if performance_improvement < improvement_threshold:
                self.epsilon = min(1.0, self.epsilon + exploration_increase)
            else:
                self.epsilon = max(self.epsilon_min, self.epsilon - exploration_decrease)

    # Update function to be called every time step
    def update(self, replay_buffer, batch_size=64, gamma=0.99):
        # Sample a batch of experiences from the replay buffer
        states, actions, rewards, next_states, dones = replay_buffer.sample(batch_size)

        states = torch.FloatTensor(states)
        actions = torch.FloatTensor(actions)
        rewards = torch.FloatTensor(rewards).unsqueeze(1)
        next_states = torch.FloatTensor(next_states)
        dones = torch.FloatTensor(dones).unsqueeze(1)

        # Compute the target Q value
        next_actions = self.actor(next_states)
        next_Q_values = self.critic(next_states, next_actions.detach())
        Q_targets = rewards + (gamma * next_Q_values * (1 - dones))
        # Compute current Q values
        Q_expected = self.critic(states, actions)

        # Critic loss
        critic_loss = F.mse_loss(Q_expected, Q_targets.detach())

        # Update critic
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()
        self.critic_scheduler.step()  # Learning rate scheduler step

        # Actor loss
        actor_loss = -self.critic(states, self.actor(states)).mean()

        # Update actor
        # Update actor
        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        self.actor_optimizer.step()
        self.actor_scheduler.step()  # Learning rate scheduler step

        # Update exploration noise
        self.adjust_epsilon_based_on_performance()

def preprocess_state(state):
    state = state / 255.0  # Normalize the input state
    state = np.transpose(state, (2, 0, 1))  # Adjust dimensions if necessary
    state = state.flatten()  # Flatten the state to create a single long vector
    state_tensor = torch.tensor(state, dtype=torch.float32).unsqueeze(0)  # Ensure dtype is torch.float32
    return state_tensor


import logging

# Set up logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def train(env, agent, episodes, save_path):
    start_time = time.time()
    total_rewards = []
    for episode in range(episodes):
        state = env.reset()
        state = preprocess_state(state)
        episode_reward = 0
        done = False

        while not done:
            action = agent.select_action(state)
            next_state, reward, done, _ = env.step(action)
            next_state = preprocess_state(next_state)

            # Assuming you have a function to store transitions in replay buffer here

            state = next_state
            episode_reward += reward

        total_rewards.append(episode_reward)
        print(f"Episode: {episode}, Total Reward: {episode_reward}")

        # Perform learning update here
        # Assuming you have a function to perform learning update here

        # Decay epsilon and update learning rate
        agent.adjust_epsilon_based_on_performance()
        # Update actor and critic networks
        agent.actor_optimizer.step()
        agent.critic_optimizer.step()

        # Step actor and critic learning rate schedulers
        agent.actor_scheduler.step()
        agent.critic_scheduler.step()

        if episode % 10 == 0:
            torch.save(agent.actor.state_dict(), save_path)

    # Output metrics
    average_reward = sum(total_rewards) / episodes
    print(f"Average Reward: {average_reward}")
    print(f"Training Time: {time.time() - start_time}s")
    logging.info(f"Average Reward: {average_reward}")
    logging.info(f"Training Time: {time.time() - start_time}s")


if __name__ == "__main__":
    conf = {
        "port": args.port,
        "body_rgb": (167, 47, 47),
        "body_style": "cybertruck",
        "car_name": "Jamie Tanner",
        "font_size": 100,
        "racer_name": "QNN",
        "country": "USA",
        "bio": "Learning to drive w qnn RL",
        "max_cte": 5.0,
    }
    env = gym.make(args.env_name, conf=conf )
    state_dim = np.product(env.observation_space.shape)
    action_dim = env.action_space.shape[0]

    agent = DDPGAgent(state_dim=state_dim, action_dim=action_dim, learning_rate=args.learning_rate)
    train(env, agent, args.episodes, args.save_model)